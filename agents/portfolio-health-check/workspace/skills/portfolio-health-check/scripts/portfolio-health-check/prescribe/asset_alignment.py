"""Asset alignment analysis: theme matching, defensive adequacy, keep-strong, unclassified ratio."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from data_loader import Holding, UserConstraints, CAPITAL_LEVELS

_WHITELIST_PATH = Path(__file__).parent / "asset_whitelist.json"


def load_whitelist(path: str | Path | None = None) -> dict[str, Any]:
    """Load the three-category asset whitelist."""
    p = Path(path) if path else _WHITELIST_PATH
    with open(p, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Theme matching
# ---------------------------------------------------------------------------

def match_holding_to_theme(holding: Holding, themes: list[dict]) -> list[dict]:
    """Match a holding against main_theme list. Returns all hits."""
    code = holding.ticker
    text_pool = " ".join(filter(None, [
        holding.sector_theme,
        holding.style_tag,
        holding.lookthrough_group,
        holding.position_name,
    ])).lower()

    matches = []
    for idx, theme in enumerate(themes):
        match_type = None

        # Rule 1: exact code match
        all_codes: set[str] = set(theme.get("reference_stocks", []))
        all_codes |= {e["code"] for e in theme.get("primary_etfs", [])}
        all_codes |= {e["code"] for e in theme.get("proxy_etfs", [])}
        if code in all_codes:
            match_type = "exact"

        # Rule 2: keyword match against text pool
        if not match_type:
            keywords = [kw.lower() for kw in theme.get("keywords", [])]
            if any(kw in text_pool for kw in keywords):
                match_type = "keyword"

        if match_type:
            matches.append({
                "theme": theme["theme_name"],
                "match_type": match_type,
                "index": idx,
            })

    return matches


def resolve_primary_theme(matches: list[dict]) -> dict | None:
    """Pick primary theme (lowest index = highest priority)."""
    return min(matches, key=lambda m: m["index"]) if matches else None


# ---------------------------------------------------------------------------
# Output 1: Theme coverage
# ---------------------------------------------------------------------------

def analyze_theme_coverage(
    holdings: list[Holding],
    themes: list[dict],
    min_theme_weight: float = 0.05,
) -> dict:
    """Analyze main theme coverage. Coverage = weight-based, not theme count."""
    all_matches = {}
    primary_map: dict[str, dict | None] = {}
    for h in holdings:
        if h.is_cash:
            continue
        ms = match_holding_to_theme(h, themes)
        all_matches[h.ticker] = ms
        primary_map[h.ticker] = resolve_primary_theme(ms)

    # Aggregate weight per theme (each holding counted only in primary theme)
    theme_weights: dict[str, float] = {}
    non_cash_total = 0.0
    for h in holdings:
        if h.is_cash:
            continue
        w = h.weight_pct / 100
        non_cash_total += w
        p = primary_map.get(h.ticker)
        if p:
            theme_weights[p["theme"]] = theme_weights.get(p["theme"], 0) + w

    hit = {t for t, w in theme_weights.items() if w >= min_theme_weight}
    all_theme_names = {t["theme_name"] for t in themes}
    coverage = sum(w for t, w in theme_weights.items() if t in hit) / non_cash_total if non_cash_total > 0 else 0

    return {
        "theme_weight_coverage": round(coverage, 4),
        "hit_themes": [
            {"name": t, "weight": round(w, 4)}
            for t, w in sorted(theme_weights.items(), key=lambda x: -x[1])
            if t in hit
        ],
        "miss_themes": sorted(all_theme_names - hit),
        "primary_map": primary_map,  # for downstream use
    }


# ---------------------------------------------------------------------------
# Output 2: Defensive adequacy
# ---------------------------------------------------------------------------

DEFENSIVE_TRIGGER_MAP = {
    "hedge_in_objectives":      lambda ctx: "hedge" in ctx["objectives"],
    "income_in_objectives":     lambda ctx: "income" in ctx["objectives"],
    "portfolio_vol_high":       lambda ctx: any(f["metric"] == "portfolio_vol_high" for f in ctx["flags"]),
    "max_dd_deep":              lambda ctx: any(f["metric"] == "max_dd_deep" for f in ctx["flags"]),
    "low_sharpe":               lambda ctx: any(f["metric"] == "low_sharpe" for f in ctx["flags"]),
    "conservative_or_moderate": lambda ctx: ctx["risk_tolerance"] in ("conservative", "moderate"),
    "equity_heavy_no_buffer":   lambda ctx: ctx["equity_pct"] > 0.85 and ctx["defensive_pct"] < 0.05,
}


def analyze_defensive_adequacy(
    holdings: list[Holding],
    whitelist_defensive: list[dict],
    context: dict,
) -> dict:
    """Check if defensive allocation is adequate. Funding-aware."""
    # Dilution for new_capital scenario
    if context.get("additional_capital_ratio", "none") != "none":
        capital_ratio = CAPITAL_LEVELS[context["additional_capital_ratio"]]
        dilution = 1 / (1 + capital_ratio)
    else:
        dilution = 1.0

    # Existing defensive holdings (diluted if new_capital)
    existing_defensive: dict[str, float] = {}
    for h in holdings:
        for d in whitelist_defensive:
            if any(e["code"] == h.ticker for e in d["primary_etfs"]):
                existing_defensive[d["asset_name"]] = (
                    existing_defensive.get(d["asset_name"], 0) + h.weight_pct * dilution
                )

    # Available funding (dilution-aware)
    cash_pct = context.get("existing_cash_pct", 0)
    if context.get("additional_capital_ratio", "none") != "none":
        capital_ratio = CAPITAL_LEVELS[context["additional_capital_ratio"]]
        available_pct = cash_pct * dilution + capital_ratio / (1 + capital_ratio) * 100
    else:
        available_pct = cash_pct

    # Score and sort by trigger count (explicit priority)
    scored = []
    for d in whitelist_defensive:
        triggered = [t for t in d["trigger_conditions"] if DEFENSIVE_TRIGGER_MAP.get(t, lambda _: False)(context)]
        scored.append((len(triggered), d, triggered))
    scored.sort(key=lambda x: -x[0])

    results = []
    remaining = available_pct
    for trigger_count, d, triggered in scored:
        current_pct = existing_defensive.get(d["asset_name"], 0)
        low, high = d["suggested_range_pct"]

        if trigger_count == 0:
            status = "not_triggered"
            feasible_low, feasible_high, feasible = 0, 0, False
        elif current_pct >= low:
            status = "adequate"
            feasible_low, feasible_high, feasible = low, high, True
        else:
            status = "missing" if current_pct == 0 else "insufficient"
            feasible_low = min(low, current_pct + remaining)
            feasible_high = min(high, current_pct + remaining)
            feasible = remaining > 1.0

        results.append({
            "asset_name": d["asset_name"],
            "triggered": trigger_count > 0,
            "trigger_reasons": triggered,
            "current_pct": round(current_pct, 1),
            "suggested_range_pct": [low, high],
            "feasible_range_pct": [round(feasible_low, 1), round(feasible_high, 1)],
            "feasible": feasible,
            "status": status,
            "etf": d["primary_etfs"][0] if d["primary_etfs"] else None,
        })

        if trigger_count > 0 and status in ("missing", "insufficient"):
            remaining -= max(feasible_low - current_pct, 0)

    return {
        "total_defensive_pct": round(sum(existing_defensive.values()), 1),
        "items": results,
    }


# ---------------------------------------------------------------------------
# Output 3: Same-track keep-strong
# ---------------------------------------------------------------------------

def _normalize(value: float, low: float, high: float) -> float:
    if high == low:
        return 0.5
    return max(0.0, min(1.0, (value - low) / (high - low)))


def score_holding_strength(
    holding_metrics: dict,
    theme_info: dict,
    code: str,
) -> float:
    """Objective strength score: sharpe 30% + dd 25% + vol 15% + liquidity 15% + reference 15%."""
    sharpe = holding_metrics.get("sharpe_ratio") or 0
    dd = holding_metrics.get("max_drawdown") or 0
    vol = holding_metrics.get("ann_volatility") or 0
    # Use weight_pct as liquidity proxy if no turnover data
    liq = math.log(max(holding_metrics.get("avg_daily_turnover", 1), 1))

    score = (
        0.30 * _normalize(sharpe, -0.5, 1.5) +
        0.25 * (1 - _normalize(dd, 0, 0.5)) +
        0.15 * (1 - _normalize(vol, 0, 0.5)) +
        0.15 * _normalize(liq, 10, 20) +
        0.15 * (1.0 if code in theme_info.get("reference_stocks", []) else 0.0)
    )
    return round(score, 4)


def analyze_same_track_keep_strong(
    holdings: list[Holding],
    holdings_metrics: list[dict],
    themes: list[dict],
    primary_map: dict[str, dict | None],
    liquidity_holdings: list[dict] | None = None,
) -> list[dict]:
    """Find same-track keep-strong opportunities."""
    metrics_map = {h["code"]: h for h in holdings_metrics}
    liq_map = {h["code"]: h for h in (liquidity_holdings or [])}

    # Group by primary theme
    by_theme: dict[str, list[Holding]] = {}
    for h in holdings:
        if h.is_cash:
            continue
        p = primary_map.get(h.ticker)
        if p:
            by_theme.setdefault(p["theme"], []).append(h)

    opportunities = []
    for theme_name, theme_holdings in by_theme.items():
        if len(theme_holdings) < 2:
            continue

        theme_info = next((t for t in themes if t["theme_name"] == theme_name), {})

        # Score each holding
        scored = []
        for h in theme_holdings:
            m = metrics_map.get(h.ticker, {})
            m_with_liq = dict(m)
            liq = liq_map.get(h.ticker, {})
            m_with_liq["avg_daily_turnover"] = liq.get("avg_daily_turnover", 0)
            s = score_holding_strength(m_with_liq, theme_info, h.ticker)
            scored.append((h, s))

        scored.sort(key=lambda x: -x[1])
        strong = scored[:1]  # keep top 1
        weak = scored[1:]    # rest are candidates

        etfs = theme_info.get("primary_etfs", [])
        proxy = theme_info.get("proxy_etfs", [])

        opportunities.append({
            "theme": theme_name,
            "holding_count": len(theme_holdings),
            "keep": [{"code": h.ticker, "name": h.position_name, "score": s} for h, s in strong],
            "replace_candidates": [{"code": h.ticker, "name": h.position_name, "score": s} for h, s in weak],
            "recommended_etf": etfs[0] if etfs else (proxy[0] if proxy else None),
            "coverage_level": theme_info.get("coverage_level", "low"),
        })

    return opportunities


# ---------------------------------------------------------------------------
# Output 4: Unclassified ratio
# ---------------------------------------------------------------------------

def compute_unclassified(
    holdings: list[Holding],
    primary_map: dict[str, dict | None],
    defensive_codes: set[str],
    base_codes: set[str],
) -> dict:
    """Compute non-whitelist asset ratio."""
    classified = set()
    for code, p in primary_map.items():
        if p:
            classified.add(code)
    classified |= defensive_codes | base_codes

    unclassified = []
    total_pct = 0.0
    for h in holdings:
        if h.is_cash:
            continue
        if h.ticker not in classified:
            unclassified.append({
                "code": h.ticker, "name": h.position_name, "weight_pct": h.weight_pct,
            })
            total_pct += h.weight_pct / 100

    return {
        "unclassified_pct": round(total_pct, 4),
        "holdings": unclassified,
    }


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------

def run_asset_alignment(
    holdings: list[Holding],
    holdings_metrics: list[dict],
    whitelist: dict,
    constraints: UserConstraints,
    risk_flags: list[dict],
    liquidity: dict | None = None,
) -> dict:
    """Run full asset alignment analysis."""
    themes = whitelist.get("main_theme", [])
    defensive = whitelist.get("defensive", [])
    base = whitelist.get("base_allocation", [])

    # Theme coverage
    coverage = analyze_theme_coverage(holdings, themes)
    primary_map = coverage.pop("primary_map")

    # Defensive adequacy
    non_cash_total = sum(h.weight_pct / 100 for h in holdings if not h.is_cash)
    # Identify existing defensive/base holdings
    defensive_codes: set[str] = set()
    for h in holdings:
        for d in defensive:
            if any(e["code"] == h.ticker for e in d["primary_etfs"]):
                defensive_codes.add(h.ticker)
    defensive_pct = sum(h.weight_pct / 100 for h in holdings if h.ticker in defensive_codes)

    base_codes: set[str] = set()
    for h in holdings:
        for b in base:
            if any(e["code"] == h.ticker for e in b.get("primary_etfs", [])):
                base_codes.add(h.ticker)

    context = {
        "objectives": constraints.objectives,
        "risk_tolerance": constraints.risk_tolerance,
        "flags": risk_flags,
        "equity_pct": non_cash_total - defensive_pct,
        "defensive_pct": defensive_pct,
        "existing_cash_pct": constraints.existing_cash_pct,
        "additional_capital_ratio": constraints.additional_capital_ratio,
    }
    defensive_result = analyze_defensive_adequacy(holdings, defensive, context)

    # Same-track keep-strong
    liq_holdings = (liquidity or {}).get("holdings", [])
    keep_strong = analyze_same_track_keep_strong(
        holdings, holdings_metrics, themes, primary_map, liq_holdings,
    )

    # Unclassified
    unclassified = compute_unclassified(holdings, primary_map, defensive_codes, base_codes)

    return {
        "main_theme": coverage,
        "defensive": defensive_result,
        "same_track_opportunities": keep_strong,
        "unclassified": unclassified,
    }

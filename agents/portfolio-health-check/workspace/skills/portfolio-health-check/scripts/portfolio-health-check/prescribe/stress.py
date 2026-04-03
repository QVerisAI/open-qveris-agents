"""Stress testing: 5 predefined shock scenarios."""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


def build_holdings_meta(
    holding_returns: dict[str, pd.Series],
    benchmark_returns: pd.Series | None,
    holdings: list,
    liquidity: dict | None,
    catalog: list[dict] | None,
    candidate_stats: dict | None = None,
) -> dict[str, dict]:
    """Build per-holding metadata for stress testing."""
    holding_map = {}
    if holdings:
        for h in holdings:
            if isinstance(h, dict):
                holding_map[h.get("ticker", "")] = h
            else:
                holding_map[getattr(h, "ticker", "")] = h
    liq_map = {l["code"]: l for l in (liquidity or {}).get("holdings", [])}
    cat_map = {c["code"]: c for c in (catalog or [])}
    stats_map = candidate_stats or {}

    meta = {}
    for code, ret in holding_returns.items():
        # Beta regression
        beta = 1.0
        if benchmark_returns is not None:
            aligned = pd.DataFrame({"h": ret, "b": benchmark_returns}).dropna()
            if len(aligned) >= 20:
                cov = aligned["h"].cov(aligned["b"])
                var_b = aligned["b"].var()
                if var_b > 0:
                    beta = cov / var_b

        h = holding_map.get(code)
        sector = ""
        if h:
            sector = getattr(h, 'sector_theme', '') or h.get('sector_theme', '') if isinstance(h, dict) else h.sector_theme

        liq = liq_map.get(code, {})
        cat = cat_map.get(code, {})
        stats = stats_map.get(code, {})

        meta[code] = {
            "beta": round(beta, 2),
            "sector": sector or cat.get("sector", ""),
            "avg_daily_turnover": liq.get("avg_daily_turnover") or stats.get("avg_daily_turnover", 0),
            "exposure_market": cat.get("exposure_market", "A-share"),
        }
    return meta


def _shock_top_sector(weights: dict[str, float], meta: dict[str, dict], shock: float) -> float:
    """Sector crash: top sector holdings drop by shock."""
    sector_weights: dict[str, float] = {}
    for code, w in weights.items():
        s = meta.get(code, {}).get("sector", "")
        sector_weights[s] = sector_weights.get(s, 0) + w
    if not sector_weights:
        return 0.0
    top_sector = max(sector_weights, key=sector_weights.get)
    loss = sum(w * shock for code, w in weights.items() if meta.get(code, {}).get("sector", "") == top_sector)
    return loss


def _shock_by_beta(weights: dict[str, float], meta: dict[str, dict], high_shock: float, low_bonus: float) -> float:
    """High beta selloff: beta>1.2 down, beta<0.8 up."""
    loss = 0.0
    for code, w in weights.items():
        b = meta.get(code, {}).get("beta", 1.0)
        if b > 1.2:
            loss += w * high_shock
        elif b < 0.8:
            loss += w * low_bonus
    return loss


def _shock_low_liquidity(weights: dict[str, float], meta: dict[str, dict], bottom_pct: float, shock: float) -> float:
    """Liquidity squeeze: lowest turnover holdings drop."""
    if not weights:
        return 0.0
    sorted_codes = sorted(weights.keys(),
                          key=lambda c: meta.get(c, {}).get("avg_daily_turnover", 0))
    n_bottom = max(1, int(len(sorted_codes) * bottom_pct))
    bottom = set(sorted_codes[:n_bottom])
    return sum(w * shock for code, w in weights.items() if code in bottom)


def _shock_market(weights: dict[str, float], meta: dict[str, dict], market_shock: float) -> float:
    """Broad market crash: all holdings down proportional to beta."""
    return sum(w * market_shock * meta.get(code, {}).get("beta", 1.0) for code, w in weights.items())


def _shock_cross_border(weights: dict[str, float], meta: dict[str, dict], shock: float) -> float:
    """FX/cross-border: non-A-share exposure down."""
    return sum(w * shock for code, w in weights.items()
               if meta.get(code, {}).get("exposure_market", "A-share") != "A-share")


STRESS_SCENARIOS = [
    ("sector_crash", "单行业暴跌", lambda w, m: _shock_top_sector(w, m, -0.20)),
    ("high_beta_selloff", "高beta杀估值", lambda w, m: _shock_by_beta(w, m, -0.15, 0.02)),
    ("liquidity_squeeze", "流动性收缩", lambda w, m: _shock_low_liquidity(w, m, 0.3, -0.25)),
    ("broad_market_crash", "市场普跌", lambda w, m: _shock_market(w, m, -0.10)),
    ("fx_cross_border", "跨境汇率冲击", lambda w, m: _shock_cross_border(w, m, -0.08)),
]


def run_stress_test(
    original_weights: dict[str, float],
    whatif_weights: dict[str, float],
    holdings_meta: dict[str, dict],
) -> list[dict]:
    """Run all 5 stress scenarios. Positive delta = candidate more resilient."""
    results = []
    for scenario_id, name, shock_fn in STRESS_SCENARIOS:
        orig_loss = shock_fn(original_weights, holdings_meta)
        whatif_loss = shock_fn(whatif_weights, holdings_meta)
        delta = whatif_loss - orig_loss  # positive = candidate lost less
        results.append({
            "scenario": scenario_id,
            "scenario_name": name,
            "original_loss": round(orig_loss, 4),
            "whatif_loss": round(whatif_loss, 4),
            "delta": round(delta, 4),
            "verdict": "better" if delta > 0 else "worse",
        })
    return results

"""Dedup, exclusive groups, composite scoring, truncation."""
from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Objective-aware metric weights for composite scoring
# ---------------------------------------------------------------------------

OBJECTIVE_METRIC_WEIGHTS = {
    "growth":   {"ann_volatility": 1.0, "max_drawdown": 1.0, "sharpe_ratio": 2.0, "max_sector_pct": 1.5, "max_pairwise_corr": 1.0, "hhi": 0.8, "factor_dominant_score": 0.8},
    "income":   {"ann_volatility": 1.5, "max_drawdown": 1.5, "sharpe_ratio": 1.0, "max_sector_pct": 1.0, "max_pairwise_corr": 1.0, "hhi": 0.8, "factor_dominant_score": 0.5},
    "hedge":    {"ann_volatility": 2.0, "max_drawdown": 2.5, "sharpe_ratio": 0.8, "max_sector_pct": 1.0, "max_pairwise_corr": 1.5, "hhi": 0.8, "factor_dominant_score": 0.8},
    "ipo_base": {"ann_volatility": 1.0, "max_drawdown": 1.0, "sharpe_ratio": 1.0, "max_sector_pct": 0.5, "max_pairwise_corr": 0.5, "hhi": 0.5, "factor_dominant_score": 0.5},
}

_ALL_METRICS = list(OBJECTIVE_METRIC_WEIGHTS["growth"].keys())
DIFFICULTY_DISCOUNT = {1: 1.0, 2: 0.8, 3: 0.5}


def compute_metric_weights(objectives: list[str]) -> dict[str, float]:
    """Multi-objective -> blended metric weights."""
    combined = {m: 0.0 for m in _ALL_METRICS}
    valid = [o for o in objectives if o in OBJECTIVE_METRIC_WEIGHTS]
    if not valid:
        valid = ["growth"]
    for obj in valid:
        w = OBJECTIVE_METRIC_WEIGHTS[obj]
        for m in _ALL_METRICS:
            combined[m] += w[m]
    n = len(valid)
    return {m: combined[m] / n for m in _ALL_METRICS}


def composite_score(rec: dict, objectives: list[str]) -> float:
    """Composite score = (improvement - cost) * difficulty * side_effect_penalty."""
    rescore = rec.get("rescore")
    if rescore is None:
        return 0.3  # unscored (e.g. derivative)

    delta = rescore.get("delta", {})
    metric_weights = compute_metric_weights(objectives)

    improvement = 0.0
    for metric, weight in metric_weights.items():
        d = delta.get(metric, 0)
        if metric == "sharpe_ratio":
            improvement += weight * d
        else:
            improvement += weight * (-d)

    # Deduct transaction cost
    tc = rescore.get("estimated_turnover_cost", 0)
    improvement -= tc * 10

    # Difficulty discount
    tier = rec.get("tier", 1)
    difficulty = DIFFICULTY_DISCOUNT.get(tier, 1.0)

    # Side effect penalty
    n_effects = len(rec.get("potential_side_effects", []))
    penalty = max(1.0 - 0.15 * n_effects, 0.3)

    return round(improvement * difficulty * penalty, 4)


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def _dedup_key(rec: dict) -> tuple:
    """(action_type, fund_destination, primary_reduce_codes).

    For rebalance/replace, key on the codes being *reduced* only,
    so that two recommendations reducing the same holding merge
    even if they differ in where freed capital goes.
    """
    category = rec.get("category", "")
    targets = rec.get("targets", [])
    instruments = rec.get("instruments", [])

    if instruments:
        dest = f"buy_external:{instruments[0]['code']}"
        affected = frozenset(
            [t["code"] for t in targets] + [i["code"] for i in instruments]
        )
    elif category in ("rebalance", "replace"):
        dest = "redistribute_internal"
        # Key only on the codes being reduced — the destination is secondary
        reduce_codes = frozenset(
            t["code"] for t in targets if t.get("direction") == "reduce"
        )
        affected = reduce_codes if reduce_codes else frozenset(
            t["code"] for t in targets
        )
    else:
        dest = "other"
        affected = frozenset(t["code"] for t in targets)

    return (category, dest, affected)


def deduplicate(recs: list[dict]) -> list[dict]:
    """Merge recommendations with same dedup key."""
    groups: dict[tuple, list[dict]] = {}
    for rec in recs:
        key = _dedup_key(rec)
        groups.setdefault(key, []).append(rec)

    merged = []
    for key, group in groups.items():
        if len(group) == 1:
            merged.append(group[0])
        else:
            base = dict(group[0])
            # Merge source_flags
            all_flags = set()
            for r in group:
                all_flags.update(r.get("source_flags", []))
            base["source_flags"] = sorted(all_flags)
            # Merge rationale
            rationales = [r["rationale"] for r in group if r.get("rationale")]
            base["rationale"] = "；".join(dict.fromkeys(rationales))
            # Take most aggressive reduction per code across all group members
            if base.get("targets"):
                # Collect all targets across group, keyed by (code, direction)
                best_reduce: dict[str, float] = {}
                all_targets_by_code: dict[str, dict] = {}
                for r in group:
                    for t in r.get("targets", []):
                        code = t["code"]
                        all_targets_by_code.setdefault(code, t)
                        if t.get("direction") == "reduce":
                            prev = best_reduce.get(code, float("inf"))
                            best_reduce[code] = min(prev, t["to_pct"])

                # Rebuild targets: keep base reduce targets with most aggressive to_pct,
                # then add any increase targets not already present
                merged_targets = []
                seen_codes = set()
                for t in base["targets"]:
                    code = t["code"]
                    seen_codes.add(code)
                    if t.get("direction") == "reduce" and code in best_reduce:
                        t = dict(t)
                        t["to_pct"] = best_reduce[code]
                    merged_targets.append(t)
                # Add increase targets from other group members
                for r in group[1:]:
                    for t in r.get("targets", []):
                        if t["code"] not in seen_codes and t.get("direction") == "increase":
                            merged_targets.append(t)
                            seen_codes.add(t["code"])
                base["targets"] = merged_targets
            merged.append(base)

    return merged


# ---------------------------------------------------------------------------
# Exclusive groups (edge table)
# ---------------------------------------------------------------------------

def build_exclusive_groups(recs: list[dict]) -> list[dict]:
    """Build exclusive groups edge table."""
    groups = []
    group_counter = 0

    # Rule 1: same flag, multiple add_asset
    by_flag: dict[str, list[dict]] = {}
    for rec in recs:
        for flag in rec.get("source_flags", []):
            by_flag.setdefault(flag, []).append(rec)

    for flag, group_recs in by_flag.items():
        add_assets = [r for r in group_recs if r.get("category") == "add_asset" and r.get("id")]
        if len(add_assets) > 1:
            group_counter += 1
            groups.append({
                "group_id": f"EXG-{group_counter}",
                "recommendation_ids": [r["id"] for r in add_assets],
                "reason": f"同为解决{flag}的替代方案，选一即可",
            })

    # Rule 2: reduce vs hedge (by hedge_scope)
    reduce_recs = [r for r in recs if r.get("category") in ("rebalance", "replace") and r.get("id")]
    hedge_recs = [r for r in recs if r.get("category") == "hedge" and r.get("derivative_strategy") and r.get("id")]

    for hr in hedge_recs:
        scope = set(hr["derivative_strategy"].get("hedge_scope", []))
        conflicting = [r for r in reduce_recs
                       if any(t["code"] in scope for t in r.get("targets", []))]
        if conflicting:
            group_counter += 1
            groups.append({
                "group_id": f"EXG-{group_counter}",
                "recommendation_ids": [r["id"] for r in conflicting] + [hr["id"]],
                "reason": f"减持与期权保护涉及相同持仓({', '.join(scope)})，互斥",
            })

    return groups


# ---------------------------------------------------------------------------
# Truncation
# ---------------------------------------------------------------------------

MAX_ITEMS = {1: 2, 2: 2, 3: 1}


def truncate_by_tier(recs: list[dict], objectives: list[str]) -> list[dict]:
    """Score, sort, and truncate: T1<=2, T2<=2, T3<=1."""
    # Score all
    for rec in recs:
        rec["composite_score"] = composite_score(rec, objectives)

    # Filter non-reject
    valid = [r for r in recs if not r.get("rescore") or r["rescore"].get("verdict") != "reject"]

    # Sort by score descending
    valid.sort(key=lambda r: -(r.get("composite_score") or 0))

    # Assign IDs and truncate per tier
    result = []
    tier_counts = {1: 0, 2: 0, 3: 0}
    id_counter = 0

    for rec in valid:
        tier = rec.get("tier", 1)
        if tier_counts.get(tier, 0) >= MAX_ITEMS.get(tier, 2):
            continue
        id_counter += 1
        rec["id"] = f"R-{id_counter:03d}"
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
        result.append(rec)

    return result

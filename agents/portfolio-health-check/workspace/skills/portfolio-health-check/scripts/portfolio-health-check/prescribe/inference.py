"""Stage A: inference engine — 9 signal inferences from Phase 2 diagnosis data."""
from __future__ import annotations

from typing import Any

from data_loader import UserConstraints


_REDUCTION_STEP_CAP_PCT = {
    "portfolio_vol_high": 8.0,
    "max_dd_deep": 10.0,
    "holding_cap": 6.0,
    "risk_contribution_imbalance": 7.0,
    "low_sharpe": 5.0,
}


def _stage_target_weight(
    current_pct: float,
    full_target_pct: float,
    signal: str,
    severity: str = "medium",
) -> float:
    """Convert a full rebalance target into a gentler first-step target."""
    if full_target_pct >= current_pct:
        return round(full_target_pct, 1)

    desired_reduction = current_pct - full_target_pct
    cap = _REDUCTION_STEP_CAP_PCT.get(signal, 6.0)
    cap *= 1.0 if severity == "high" else 0.8
    cap = min(cap, current_pct * 0.35)
    step_reduction = min(desired_reduction, max(cap, 2.0))
    staged_target = max(full_target_pct, current_pct - step_reduction)
    return round(staged_target, 1)


def run_all_inferences(
    diagnosis_data: dict[str, Any],
    constraints: UserConstraints,
) -> list[dict]:
    """Run all 9 signal inferences. Returns list of inference dicts."""
    flags = diagnosis_data.get("risk_flags", [])
    flag_map = {f["metric"]: f for f in flags}
    rm = diagnosis_data.get("risk_metrics", {})
    portfolio_metrics = rm.get("portfolio", {})
    holdings_metrics = rm.get("holdings", [])
    rc = diagnosis_data.get("risk_contribution", {})
    concentration = diagnosis_data.get("concentration", {})
    corr = diagnosis_data.get("correlation_matrix", {})
    factor_exp = diagnosis_data.get("factor_exposure", {})
    sector_exp = diagnosis_data.get("sector_exposure", {})
    liquidity = diagnosis_data.get("liquidity", {})

    inferences = []

    if "portfolio_vol_high" in flag_map:
        inferences.append(_infer_vol_high(flag_map["portfolio_vol_high"], holdings_metrics, rc, constraints))
    if "max_dd_deep" in flag_map:
        inferences.append(_infer_dd_deep(flag_map["max_dd_deep"], holdings_metrics, rc))
    if "holding_cap" in flag_map:
        inferences.append(_infer_holding_cap(flag_map["holding_cap"], holdings_metrics, concentration))
    if "sector_cap" in flag_map:
        inferences.append(_infer_sector_cap(flag_map["sector_cap"], holdings_metrics, sector_exp))
    if "low_sharpe" in flag_map:
        inferences.append(_infer_low_sharpe(flag_map["low_sharpe"], holdings_metrics, portfolio_metrics, constraints))
    for flag in flags:
        if flag["metric"] == "high_correlation":
            inferences.append(_infer_high_correlation(flag, holdings_metrics, factor_exp))
    for flag in flags:
        if flag["metric"] == "factor_dominance":
            inferences.append(_infer_factor_dominance(flag, factor_exp))

    # Non-flag signals
    liq_inf = _infer_liquidity_tight(liquidity)
    if liq_inf["has_issue"]:
        inferences.append(liq_inf)

    rc_inf = _infer_risk_contribution_imbalance(rc, holdings_metrics)
    if rc_inf["has_issue"]:
        inferences.append(rc_inf)

    return inferences


# ---------------------------------------------------------------------------
# Per-signal inference functions
# ---------------------------------------------------------------------------

def _infer_vol_high(flag: dict, holdings: list[dict], rc: dict, constraints: UserConstraints) -> dict:
    """Identify holdings contributing disproportionately to portfolio volatility."""
    rc_by = {h["code"]: h for h in rc.get("by_holding", [])}
    h_map = {h["code"]: h for h in holdings}
    problem = []

    for h in holdings:
        code = h["code"]
        rc_entry = rc_by.get(code, {})
        weight_norm = rc_entry.get("weight_normalized", 0)
        pct_rc = rc_entry.get("pct_risk_contribution", 0)
        risk_ratio = pct_rc / weight_norm if weight_norm > 0.01 else 0

        if risk_ratio > 1.0:
            gap = flag["actual_value"] - flag["threshold"]
            vol = h.get("ann_volatility", 0)
            port_vol = flag["actual_value"]
            if vol > port_vol and vol != port_vol:
                reduce_est = gap / (vol - port_vol) * (h["weight_pct"] / 100)
                full_target = max(h["weight_pct"] - reduce_est * 100, 0)
            else:
                full_target = h["weight_pct"] * 0.7

            multiplier = 1.2 if "hedge" in constraints.objectives else 1.0
            full_target = max(h["weight_pct"] - (h["weight_pct"] - full_target) * multiplier, 0)
            # Avoid showing an unrealistic "ultimate target" that feels like a one-shot liquidation.
            full_target = max(full_target, h["weight_pct"] * 0.7)
            staged_target = _stage_target_weight(
                h["weight_pct"],
                full_target,
                "portfolio_vol_high",
                "high",
            )

            problem.append({
                "code": code, "name": h.get("name", code),
                "weight_pct": h["weight_pct"], "vol": vol,
                "risk_contrib_pct": round(pct_rc * 100, 1),
                "risk_ratio": round(risk_ratio, 2),
                "suggested_new_weight_pct": staged_target,
                "full_target_weight_pct": round(full_target, 1),
            })

    problem.sort(key=lambda x: -x["risk_ratio"])

    # Find best internal target (lowest vol holding)
    best_internal = sorted(holdings, key=lambda h: h.get("ann_volatility", 999))
    best_targets = [{"code": h["code"], "name": h.get("name", ""), "vol": h.get("ann_volatility", 0),
                     "weight_pct": h.get("weight_pct", 0)}
                    for h in best_internal[:2] if h["code"] not in {p["code"] for p in problem}]

    return {
        "signal": "portfolio_vol_high",
        "severity": "high",
        "gap": round(flag["actual_value"] - flag["threshold"], 4),
        "problem_holdings": problem,
        "best_internal_targets": best_targets,
        "need_external_addition": len(problem) > 0 and flag["actual_value"] - flag["threshold"] > 0.05,
    }


def _infer_dd_deep(flag: dict, holdings: list[dict], rc: dict) -> dict:
    """Identify holdings dragging down portfolio via deep drawdowns."""
    port_dd = flag["actual_value"]
    drag = []
    for h in holdings:
        dd = h.get("max_drawdown", 0)
        if dd > port_dd:
            weight_frac = h["weight_pct"] / 100
            full_target = h["weight_pct"] * (0.65 if h.get("max_dd_recovery_days") is None else 0.8)
            drag.append({
                "code": h["code"], "name": h.get("name", ""),
                "dd": round(dd, 4),
                "recovered": h.get("max_dd_recovery_days") is not None,
                "weight_pct": h["weight_pct"],
                "dd_impact": round(dd * weight_frac, 4),
                "suggested_new_weight_pct": _stage_target_weight(
                    h["weight_pct"],
                    full_target,
                    "max_dd_deep",
                    "high",
                ),
                "full_target_weight_pct": round(full_target, 1),
            })
    drag.sort(key=lambda x: -x["dd_impact"])

    return {
        "signal": "max_dd_deep",
        "severity": "high",
        "gap": round(flag["actual_value"] - flag["threshold"], 4),
        "drag_holdings": drag,
        "portfolio_still_in_drawdown": any(not d["recovered"] for d in drag),
    }


def _infer_holding_cap(flag: dict, holdings: list[dict], concentration: dict) -> dict:
    """Identify overweight holdings."""
    threshold = flag["threshold"]
    overweight = []
    for h in holdings:
        w = h["weight_pct"] / 100
        if w > threshold:
            threshold_pct = round(threshold * 100, 1)
            overweight.append({
                "code": h["code"], "name": h.get("name", ""),
                "weight_pct": h["weight_pct"],
                "threshold_pct": threshold_pct,
                "excess_pct": round((w - threshold) * 100, 1),
                "suggested_new_weight_pct": _stage_target_weight(
                    h["weight_pct"],
                    threshold_pct,
                    "holding_cap",
                    "high",
                ),
                "full_target_weight_pct": threshold_pct,
            })
    overweight.sort(key=lambda x: -x["excess_pct"])

    eff_n = concentration.get("effective_n", 0)
    return {
        "signal": "holding_cap",
        "severity": "high",
        "overweight_holdings": overweight,
        "remaining_effective_n": round(eff_n, 2),
        "need_new_positions": eff_n < 5,
    }


def _infer_sector_cap(flag: dict, holdings: list[dict], sector_exp: dict) -> dict:
    """Identify overweight sectors and missing sectors."""
    threshold = flag["threshold"]
    overweight = []
    for sector, pct in sector_exp.items():
        if pct > threshold:
            # Find worst holding in this sector by sharpe
            sector_holdings = [h for h in holdings if h.get("sector_theme", "") == sector or sector in h.get("sector_theme", "")]
            worst = min(sector_holdings, key=lambda h: h.get("sharpe_ratio") or -999) if sector_holdings else None
            overweight.append({
                "sector": sector, "pct": round(pct, 4),
                "threshold": round(threshold, 4),
                "excess": round(pct - threshold, 4),
                "trim_candidate": {
                    "code": worst["code"], "name": worst.get("name", ""),
                    "sharpe": worst.get("sharpe_ratio"),
                } if worst else None,
            })
    overweight.sort(key=lambda x: -x["excess"])

    major = {"消费", "科技", "医药", "金融", "制造", "能源", "地产", "公用事业"}
    present = set(sector_exp.keys())
    missing = sorted(major - present)

    return {
        "signal": "sector_cap",
        "severity": "high",
        "overweight_sectors": overweight,
        "missing_sectors": missing,
    }


def _infer_low_sharpe(flag: dict, holdings: list[dict], portfolio_metrics: dict, constraints: UserConstraints) -> dict:
    """Identify low-sharpe holdings and diagnose root cause."""
    port_sharpe = portfolio_metrics.get("sharpe_ratio") or 0
    worst = []
    best = []
    for h in holdings:
        s = h.get("sharpe_ratio")
        if s is None:
            continue
        if s < 0 or (port_sharpe > 0 and s < port_sharpe * 0.5):
            ret = h.get("ann_return_arithmetic", 0)
            vol = h.get("ann_volatility", 0)
            diagnosis = "negative_return" if ret < 0.015 else ("high_vol" if vol > portfolio_metrics.get("ann_volatility", 0) * 2 else "mixed")
            worst.append({"code": h["code"], "name": h.get("name", ""), "sharpe": round(s, 2),
                          "return": round(ret, 4), "vol": round(vol, 4), "diagnosis": diagnosis,
                          "weight_pct": h.get("weight_pct", 0)})
        elif port_sharpe > 0 and s > port_sharpe * 1.5:
            best.append({"code": h["code"], "name": h.get("name", ""), "sharpe": round(s, 2),
                         "weight_pct": h.get("weight_pct", 0)})

    worst.sort(key=lambda x: x["sharpe"])
    best.sort(key=lambda x: -x["sharpe"])

    # Root cause
    avg_ret = sum(h.get("ann_return_arithmetic", 0) for h in holdings) / max(len(holdings), 1)
    avg_vol = sum(h.get("ann_volatility", 0) for h in holdings) / max(len(holdings), 1)
    port_vol = portfolio_metrics.get("ann_volatility", 0)

    if "income" in constraints.objectives:
        root_cause = "low_return"
    elif avg_ret < 0.015:
        root_cause = "low_return"
    elif avg_vol > port_vol * 2:
        root_cause = "high_vol"
    else:
        root_cause = "mixed"

    return {
        "signal": "low_sharpe",
        "severity": "medium",
        "worst_holdings": worst[:3],
        "best_holdings": best[:3],
        "root_cause": root_cause,
    }


def _infer_high_correlation(flag: dict, holdings: list[dict], factor_exp: dict) -> dict:
    """For a high-correlation pair, determine which to replace."""
    pair = flag.get("actual_value", 0)
    pair_codes = []
    explanation = flag.get("explanation", "")

    # Extract pair codes from the flag (stored in explanation or as metadata)
    # The flag structure has the pair info embedded
    # We need to parse it from the flag dict directly
    # In risk_flags.py, high_correlation flags store pair info
    h_map = {h["code"]: h for h in holdings}

    # Try to get pair from flag metadata
    pair_info = flag.get("pair", [])
    if not pair_info and "pair" not in flag:
        # Fall back: can't determine pair
        return {
            "signal": "high_correlation",
            "severity": "medium",
            "correlated_pairs": [],
        }

    code_a, code_b = pair_info[0], pair_info[1] if len(pair_info) >= 2 else ("", "")
    h_a = h_map.get(code_a, {})
    h_b = h_map.get(code_b, {})
    sharpe_a = h_a.get("sharpe_ratio") or 0
    sharpe_b = h_b.get("sharpe_ratio") or 0

    if sharpe_a >= sharpe_b:
        keep, replace = h_a, h_b
    else:
        keep, replace = h_b, h_a

    return {
        "signal": "high_correlation",
        "severity": "medium",
        "correlated_pairs": [{
            "keep": {"code": keep.get("code", ""), "name": keep.get("name", ""),
                     "sharpe": round(keep.get("sharpe_ratio") or 0, 2)},
            "replace_candidate": {"code": replace.get("code", ""), "name": replace.get("name", ""),
                                   "sharpe": round(replace.get("sharpe_ratio") or 0, 2)},
            "correlation": round(flag.get("actual_value", 0), 2),
            "shared_sector": keep.get("sector_theme", ""),
        }],
    }


def _infer_factor_dominance(flag: dict, factor_exp: dict) -> dict:
    """Identify dominant factor and top contributors."""
    portfolio_factors = factor_exp.get("portfolio", {})
    holdings_factors = factor_exp.get("holdings", {})

    # Find which factor is dominant from the flag
    # The flag explanation contains the factor name
    dominant_factor = None
    dominant_score = flag.get("actual_value", 0)
    threshold = flag.get("threshold", 0)

    for f, score in portfolio_factors.items():
        if abs(score) > threshold and abs(score - dominant_score) < 0.01:
            dominant_factor = f
            break

    if not dominant_factor:
        # Fallback: find the factor with highest absolute score
        dominant_factor = max(portfolio_factors, key=lambda f: abs(portfolio_factors[f])) if portfolio_factors else None

    if not dominant_factor:
        return {"signal": "factor_dominance", "severity": "medium", "dominant_factors": []}

    # Find top contributors
    contributors = []
    for code, factors in holdings_factors.items():
        score = factors.get(dominant_factor, 0)
        contributors.append({"code": code, "score": round(score, 2)})
    contributors.sort(key=lambda x: -abs(x["score"]))

    factor_cn = {"size": "市值", "value": "价值", "momentum": "动量",
                 "quality": "质量", "volatility": "波动", "liquidity": "流动性"}.get(dominant_factor, dominant_factor)

    return {
        "signal": "factor_dominance",
        "severity": "medium",
        "dominant_factors": [{
            "factor": dominant_factor,
            "factor_cn": factor_cn,
            "score": round(dominant_score, 2),
            "threshold": round(threshold, 2),
            "top_contributors": contributors[:3],
            "offset_profile": {dominant_factor: "negative" if dominant_score > 0 else "positive"},
        }],
    }


def _infer_liquidity_tight(liquidity: dict) -> dict:
    """Flag holdings with liquidation_days > 5."""
    max_days = liquidity.get("portfolio_max_liquidation_days")
    tight = []
    for h in liquidity.get("holdings", []):
        days = h.get("liquidation_days")
        if days is not None and days > 5:
            tight.append({
                "code": h["code"],
                "liquidation_days": round(days, 1),
                "avg_daily_turnover": h.get("avg_daily_turnover", 0),
            })
    tight.sort(key=lambda x: -x["liquidation_days"])

    return {
        "signal": "liquidity_tight",
        "severity": "medium",
        "tight_holdings": tight,
        "has_issue": len(tight) > 0,
    }


def _infer_risk_contribution_imbalance(rc: dict, holdings: list[dict]) -> dict:
    """Flag holdings where pct_risk_contribution > 2x weight."""
    imbalanced = []
    for entry in rc.get("by_holding", []):
        w = entry.get("weight_normalized", 0)
        pct_rc = entry.get("pct_risk_contribution", 0)
        if w > 0.01:
            ratio = pct_rc / w
            if ratio > 2.0:
                h = next((h for h in holdings if h["code"] == entry["code"]), {})
                current_pct = round(w * 100, 1)
                full_target = current_pct / ratio
                imbalanced.append({
                    "code": entry["code"],
                    "name": h.get("name", ""),
                    "weight_pct": current_pct,
                    "risk_contrib_pct": round(pct_rc * 100, 1),
                    "ratio": round(ratio, 2),
                    "suggested_new_weight_pct": _stage_target_weight(
                        current_pct,
                        full_target,
                        "risk_contribution_imbalance",
                        "medium",
                    ),
                    "full_target_weight_pct": round(full_target, 1),
                })
    imbalanced.sort(key=lambda x: -x["ratio"])

    return {
        "signal": "risk_contribution_imbalance",
        "severity": "medium",
        "imbalanced_holdings": imbalanced,
        "has_issue": len(imbalanced) > 0,
    }

"""Stage C: rescore — what-if construction, multi-path simulation, verdict."""
from __future__ import annotations

from copy import deepcopy

import pandas as pd

from data_loader import CAPITAL_LEVELS, UserConstraints
from compute.correlation import compute_correlation_matrix, flag_high_correlations
from compute.factor_exposure import compute_portfolio_factor_exposure
from compute.risk_metrics import compute_all_metrics
from compute.concentration import compute_hhi

COST_RATE = 0.003  # single-side cost (commission + stamp duty + slippage)


def build_whatif_portfolio(
    original_weights: dict[str, float],
    original_cash_frac: float,
    recommendation: dict,
    constraints: UserConstraints,
) -> tuple[dict[str, float], float]:
    """Build what-if weights. Returns (new_weights, new_cash_frac)."""
    pinned: dict[str, float] = {}
    pinned_codes: set[str] = set()

    for target in recommendation.get("targets", []):
        pinned[target["code"]] = target["to_pct"] / 100
        pinned_codes.add(target["code"])

    for inst in recommendation.get("instruments", []):
        pinned[inst["code"]] = inst["suggested_pct"] / 100
        pinned_codes.add(inst["code"])

    untouched = {k: v for k, v in original_weights.items() if k not in pinned_codes}

    if recommendation.get("funding_req") == "new_capital":
        capital_ratio = CAPITAL_LEVELS.get(constraints.additional_capital_ratio, 0)
        if capital_ratio > 0:
            dilution = 1 / (1 + capital_ratio)
            untouched = {k: v * dilution for k, v in untouched.items()}

    new_weights = dict(untouched)
    new_weights.update(pinned)
    new_cash_frac = max(1.0 - sum(new_weights.values()), 0.0)
    return new_weights, new_cash_frac


def estimate_turnover_cost(
    original_weights: dict[str, float],
    whatif_weights: dict[str, float],
) -> float:
    """Estimated transaction cost as fraction of total assets."""
    all_codes = set(original_weights) | set(whatif_weights)
    turnover = sum(abs(whatif_weights.get(c, 0) - original_weights.get(c, 0)) for c in all_codes) / 2
    return round(turnover * 2 * COST_RATE, 6)


def rescore_candidate(
    recommendation: dict,
    original_weights: dict[str, float],
    original_cash_frac: float,
    original_metrics: dict,
    holding_returns: dict[str, pd.Series],
    constraints: UserConstraints,
    ann_factor: int = 252,
    holdings: list | None = None,
    factor_scores: dict | None = None,
    corr_threshold: float = 0.7,
) -> dict:
    """Rescore a candidate recommendation. Returns rescore dict."""
    step_eval = _evaluate_plan(
        recommendation,
        original_weights,
        original_cash_frac,
        original_metrics,
        holding_returns,
        constraints,
        ann_factor,
        factor_scores,
    )
    if step_eval is None:
        return _empty_rescore(original_metrics)

    simulation_cases = [_case_payload("先做一步", step_eval)]
    full_rec = _materialize_plan(recommendation, use_full_target=True)
    if _has_material_full_case(recommendation):
        full_eval = _evaluate_plan(
            full_rec,
            original_weights,
            original_cash_frac,
            original_metrics,
            holding_returns,
            constraints,
            ann_factor,
            factor_scores,
        )
        if full_eval is not None:
            simulation_cases.append(_case_payload("做到目标区间", full_eval))

    verdict, side_effects = _determine_verdict(step_eval["delta"], recommendation.get("tier"))

    return {
        "original": original_metrics,
        "whatif": step_eval["whatif"],
        "delta": step_eval["delta"],
        "verdict": verdict,
        "estimated_turnover_cost": step_eval["estimated_turnover_cost"],
        "stress_test": [],
        "capital_required": _estimate_capital_required(recommendation, constraints),
        "side_effects": side_effects,
        "simulation_cases": simulation_cases,
        "headline": _build_headline(step_eval, simulation_cases[1] if len(simulation_cases) > 1 else None),
        "beginner_summary": _build_beginner_summary(step_eval, simulation_cases[1] if len(simulation_cases) > 1 else None),
    }


def _materialize_plan(recommendation: dict, use_full_target: bool) -> dict:
    rec = deepcopy(recommendation)
    if not use_full_target:
        return rec

    for target in rec.get("targets", []):
        if "full_target_pct" in target:
            target["to_pct"] = target["full_target_pct"]
    for inst in rec.get("instruments", []):
        if "full_target_pct" in inst:
            inst["suggested_pct"] = inst["full_target_pct"]
    return rec


def _has_material_full_case(recommendation: dict) -> bool:
    for target in recommendation.get("targets", []):
        if abs(target.get("full_target_pct", target.get("to_pct", 0)) - target.get("to_pct", 0)) >= 0.5:
            return True
    for inst in recommendation.get("instruments", []):
        if abs(inst.get("full_target_pct", inst.get("suggested_pct", 0)) - inst.get("suggested_pct", 0)) >= 0.5:
            return True
    return False


def _evaluate_plan(
    recommendation: dict,
    original_weights: dict[str, float],
    original_cash_frac: float,
    original_metrics: dict,
    holding_returns: dict[str, pd.Series],
    constraints: UserConstraints,
    ann_factor: int,
    factor_scores: dict | None,
) -> dict | None:
    new_weights, new_cash = build_whatif_portfolio(
        original_weights,
        original_cash_frac,
        recommendation,
        constraints,
    )
    available_codes = [c for c in new_weights if c in holding_returns]
    if not available_codes:
        return None

    rf_per_period = 0.015 / ann_factor
    port_ret = None
    for code in available_codes:
        weighted = holding_returns[code] * new_weights[code]
        port_ret = weighted if port_ret is None else port_ret.add(weighted, fill_value=0)
    if port_ret is None:
        return None
    if new_cash > 0:
        port_ret = port_ret + new_cash * rf_per_period

    whatif_pm = compute_all_metrics(port_ret, ann_factor)
    non_cash_weights = list(new_weights.values())
    total_nc = sum(non_cash_weights)
    norm_weights = [w / total_nc for w in non_cash_weights] if total_nc > 0 else []
    whatif_hhi = compute_hhi(norm_weights)

    whatif_returns = {c: holding_returns[c] for c in available_codes}
    if len(whatif_returns) >= 2:
        corr_matrix = compute_correlation_matrix(whatif_returns)
        high_pairs = flag_high_correlations(corr_matrix, 0.0)
        whatif_max_corr = max((abs(p["correlation"]) for p in high_pairs), default=0)
    else:
        whatif_max_corr = 0.0

    whatif_factor_dominant = original_metrics.get("factor_dominant_score", 0)
    if factor_scores:
        fe = compute_portfolio_factor_exposure(factor_scores, new_weights, new_cash)
        portfolio_factors = fe.get("portfolio", {})
        if portfolio_factors:
            whatif_factor_dominant = max(abs(v) for v in portfolio_factors.values())

    whatif_metrics = {
        "ann_volatility": whatif_pm.get("ann_volatility", 0),
        "max_drawdown": whatif_pm.get("max_drawdown", 0),
        "sharpe_ratio": whatif_pm.get("sharpe_ratio", 0),
        "max_sector_pct": original_metrics.get("max_sector_pct", 0),
        "max_pairwise_corr": round(whatif_max_corr, 4),
        "hhi": round(whatif_hhi, 4),
        "factor_dominant_score": round(whatif_factor_dominant, 4),
    }

    delta = {}
    for key in whatif_metrics:
        orig = original_metrics.get(key, 0) or 0
        delta[key] = round(whatif_metrics[key] - orig, 4)

    return {
        "weights": new_weights,
        "cash": new_cash,
        "whatif": whatif_metrics,
        "delta": delta,
        "estimated_turnover_cost": estimate_turnover_cost(original_weights, new_weights),
    }


def _case_payload(name: str, evaluation: dict) -> dict:
    return {
        "name": name,
        "whatif": evaluation["whatif"],
        "delta": evaluation["delta"],
        "estimated_turnover_cost": evaluation["estimated_turnover_cost"],
        "summary": _summarize_case(evaluation["delta"]),
    }


def _summarize_case(delta: dict) -> str:
    parts = []
    if delta.get("ann_volatility", 0) < 0:
        parts.append(f"波动下降{abs(delta['ann_volatility']) * 100:.1f}个百分点")
    if delta.get("max_drawdown", 0) < 0:
        parts.append(f"最大回撤改善{abs(delta['max_drawdown']) * 100:.1f}个百分点")
    if delta.get("sharpe_ratio", 0) > 0:
        parts.append(f"夏普提升{delta['sharpe_ratio']:.2f}")
    if not parts:
        parts.append("改善幅度有限，主要价值在于先验证方向是否适配")
    return "，".join(parts)


def _determine_verdict(delta: dict, tier: int | None) -> tuple[str, list[str]]:
    side_effects: list[str] = []
    worsening_risks: list[str] = []
    risk_improvement = 0.0
    for key, value in delta.items():
        if key == "sharpe_ratio":
            if value < -0.01:
                side_effects.append(f"夏普比率下降{abs(value):.2f}")
        elif value > 0.01:
            worsening_risks.append(f"{_metric_cn(key)}上升{_fmt_metric(value, key)}")
        elif value < -0.01:
            risk_improvement += abs(value)

    side_effects.extend(worsening_risks)

    if worsening_risks:
        verdict = "pass_with_warning"
    elif side_effects:
        # Small-step defensive moves often trade a little Sharpe for much lower drawdown.
        verdict = "pass_with_warning" if risk_improvement >= 0.08 else "reject"
    else:
        verdict = "pass"

    if tier == 1 and verdict == "reject" and risk_improvement >= 0.08:
        verdict = "pass_with_warning"
    return verdict, side_effects


def _metric_cn(metric: str) -> str:
    return {
        "ann_volatility": "年化波动率",
        "max_drawdown": "最大回撤",
        "max_sector_pct": "最大行业暴露",
        "max_pairwise_corr": "最高相关性",
        "hhi": "集中度",
        "factor_dominant_score": "因子偏移度",
    }.get(metric, metric)


def _fmt_metric(value: float, metric: str) -> str:
    if metric in {"ann_volatility", "max_drawdown", "max_sector_pct", "hhi"}:
        return f"{value * 100:.1f}%"
    return f"{value:.2f}"


def _build_headline(step_eval: dict, full_case: dict | None) -> str:
    step_summary = _summarize_case(step_eval["delta"])
    if full_case:
        return f"先做一步时，{step_summary}；若继续做到目标区间，{full_case['summary']}。"
    return f"按当前建议执行后，{step_summary}。"


def _build_beginner_summary(step_eval: dict, full_case: dict | None) -> str:
    step = step_eval["whatif"]
    text = (
        f"先做一步后，组合年化波动预计在{step.get('ann_volatility', 0) * 100:.1f}%附近，"
        f"最大回撤预计在{step.get('max_drawdown', 0) * 100:.1f}%附近。"
    )
    if full_case:
        full = full_case["whatif"]
        text += (
            f" 如果后续继续做到目标区间，波动有机会进一步降到"
            f"{full.get('ann_volatility', 0) * 100:.1f}%，但换手成本也会更高。"
        )
    return text


def _estimate_capital_required(
    recommendation: dict,
    constraints: UserConstraints,
) -> dict | None:
    if recommendation.get("funding_req") != "new_capital":
        return None
    target_pct = sum(inst.get("suggested_pct", 0) for inst in recommendation.get("instruments", []))
    return {
        "mode": "new_capital",
        "allowed_ratio": constraints.additional_capital_ratio,
        "estimated_step_pct": round(target_pct, 1),
    }


def _empty_rescore(original_metrics: dict) -> dict:
    return {
        "original": original_metrics,
        "whatif": original_metrics,
        "delta": {k: 0 for k in original_metrics},
        "verdict": "pass",
        "estimated_turnover_cost": 0,
        "stress_test": [],
        "capital_required": None,
        "side_effects": [],
        "simulation_cases": [],
        "headline": "缺少可用价格序列，暂时无法做回验模拟。",
        "beginner_summary": "缺少可用价格序列，暂时无法估算调整前后的差异。",
    }

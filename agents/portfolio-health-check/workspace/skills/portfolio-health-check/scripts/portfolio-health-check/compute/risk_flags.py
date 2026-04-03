"""Risk flags: 7 threshold checks + informational warnings."""
from __future__ import annotations

from typing import Optional

from compute.thresholds import get_thresholds, apply_horizon_adjustment

# ── Chinese translations ─────────────────────────────────────────────

_CN_TOLERANCE = {
    "conservative": "保守型", "moderate": "稳健型",
    "aggressive": "积极型", "very_aggressive": "激进型",
}

_CN_METRIC = {
    "portfolio_vol_high": "组合波动率过高",
    "max_dd_deep": "最大回撤过深",
    "holding_cap": "单只持仓占比超限",
    "sector_cap": "单行业占比超限",
    "low_sharpe": "夏普比率偏低",
    "high_correlation": "持仓间高相关",
    "factor_dominance": "单因子暴露过高",
}

_CN_FACTOR = {
    "size": "市值", "value": "价值", "momentum": "动量",
    "quality": "质量", "volatility": "波动", "liquidity": "流动性",
}

_CN_SEVERITY = {"high": "高", "medium": "中"}


def evaluate_flags(
    portfolio_metrics: dict,
    corr_high_pairs: list[dict],
    portfolio_factors: dict[str, float],
    concentration: dict,
    risk_tolerance: str,
    investment_horizon: str,
) -> list[dict]:
    """Evaluate 7 risk flags. All output text is Chinese."""
    base = get_thresholds(risk_tolerance)
    adj = apply_horizon_adjustment(base, investment_horizon)
    tol_cn = _CN_TOLERANCE.get(risk_tolerance, risk_tolerance)

    flags: list[dict] = []

    # 1. portfolio_vol_high
    _check(flags, "high", "portfolio_vol_high",
           portfolio_metrics.get("ann_volatility"),
           adj["portfolio_vol_high"],
           lambda a, t: a > t,
           f"组合年化波动率为{{actual:.1%}}，超出{tol_cn}投资者的参考上限{{threshold:.1%}}。波动率越高，账户金额日常涨跌幅越大。")

    # 2. max_dd_deep
    _check(flags, "high", "max_dd_deep",
           portfolio_metrics.get("max_drawdown"),
           adj["max_dd_deep"],
           lambda a, t: a > t,
           f"组合最大回撤达到{{actual:.1%}}，超出{tol_cn}投资者的容忍上限{{threshold:.1%}}。这意味着在分析期内，组合从最高点到最低点的跌幅较大。")

    # 3. holding_cap
    max_h = concentration.get("max_holding_pct")
    _check(flags, "high", "holding_cap",
           max_h, adj["holding_cap"],
           lambda a, t: a > t,
           f"单只持仓最大占比为{{actual:.1%}}，超出{tol_cn}投资者的建议上限{{threshold:.1%}}。过度集中于单只标的会放大个股风险。")

    # 4. sector_cap
    max_s = concentration.get("max_sector_pct")
    _check(flags, "high", "sector_cap",
           max_s, adj["sector_cap"],
           lambda a, t: a > t,
           f"单一行业最大占比为{{actual:.1%}}，超出{tol_cn}投资者的建议上限{{threshold:.1%}}。行业过于集中会增加政策和周期风险。")

    # 5. low_sharpe
    _check(flags, "medium", "low_sharpe",
           portfolio_metrics.get("sharpe_ratio"),
           adj["low_sharpe"],
           lambda a, t: a is not None and a < t,
           f"组合夏普比率为{{actual:.2f}}，低于{tol_cn}投资者的参考值{{threshold:.2f}}。夏普比率反映每承担一份波动所获得的超额回报，数值越高越好。")

    # 6. high_correlation
    if adj["high_correlation"] is not None:
        for pair in corr_high_pairs:
            if abs(pair["correlation"]) > adj["high_correlation"]:
                flags.append({
                    "severity": "medium",
                    "metric": "high_correlation",
                    "metric_cn": _CN_METRIC["high_correlation"],
                    "severity_cn": _CN_SEVERITY["medium"],
                    "actual_value": pair["correlation"],
                    "threshold": adj["high_correlation"],
                    "explanation": (
                        f"{pair['pair'][0]} 与 {pair['pair'][1]} 的相关系数为 {pair['correlation']:.2f}，"
                        f"超出{tol_cn}投资者的阈值 {adj['high_correlation']:.2f}。"
                        f"高相关意味着这两只标的倾向于同涨同跌，分散效果有限。"
                    ),
                })

    # 7. factor_dominance
    if adj["factor_dominance"] is not None:
        for factor, score in portfolio_factors.items():
            if abs(score) > adj["factor_dominance"]:
                factor_cn = _CN_FACTOR.get(factor, factor)
                flags.append({
                    "severity": "medium",
                    "metric": "factor_dominance",
                    "metric_cn": _CN_METRIC["factor_dominance"],
                    "severity_cn": _CN_SEVERITY["medium"],
                    "actual_value": round(score, 2),
                    "threshold": adj["factor_dominance"],
                    "explanation": (
                        f"组合在「{factor_cn}」因子上的暴露为 {score:.2f}，"
                        f"绝对值超出{tol_cn}投资者的阈值 {adj['factor_dominance']:.2f}。"
                        f"这说明组合对{factor_cn}风格有较强依赖，当该风格不受市场青睐时可能表现不佳。"
                    ),
                })

    # Sort: high first, then by excess ratio desc
    def _sort_key(f):
        sev = 0 if f["severity"] == "high" else 1
        if f["threshold"] is not None and f["threshold"] != 0:
            excess = abs(f["actual_value"] / f["threshold"]) if f["actual_value"] is not None else 0
        else:
            excess = 0
        return (sev, -excess)

    flags.sort(key=_sort_key)
    return flags


def _check(
    flags: list[dict],
    severity: str,
    metric: str,
    actual: Optional[float],
    threshold: Optional[float],
    condition,
    explanation_template: str,
):
    if threshold is None or actual is None:
        return
    if condition(actual, threshold):
        flags.append({
            "severity": severity,
            "metric": metric,
            "metric_cn": _CN_METRIC.get(metric, metric),
            "severity_cn": _CN_SEVERITY.get(severity, severity),
            "actual_value": round(actual, 4),
            "threshold": round(threshold, 4),
            "explanation": explanation_template.format(actual=actual, threshold=threshold),
        })

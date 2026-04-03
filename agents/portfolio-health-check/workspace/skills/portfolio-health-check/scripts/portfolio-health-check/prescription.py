"""Phase 3 prescription orchestrator — wires Stage A/B/C + asset alignment."""
from __future__ import annotations

from typing import Any

import pandas as pd

from data_loader import Holding, UserConstraints
from prescribe.asset_alignment import load_whitelist, run_asset_alignment
from prescribe.dedup import build_exclusive_groups, deduplicate, truncate_by_tier
from prescribe.inference import run_all_inferences
from prescribe.mapping import map_inferences_to_recommendations
from prescribe.rescore import build_whatif_portfolio, rescore_candidate
from prescribe.stress import build_holdings_meta, run_stress_test
from structured_output import build_prescription_client_output


def classify_entry(risk_flags: list[dict]) -> str:
    """Classify entry path: no_action / light_touch / actionable."""
    high = [f for f in risk_flags if f["severity"] == "high"]
    medium = [f for f in risk_flags if f["severity"] == "medium"]
    if not high and not medium:
        return "no_action"
    if not high and len(medium) <= 1:
        return "light_touch"
    return "actionable"


def run_prescription(
    diagnosis_data: dict[str, Any],
    constraints: UserConstraints,
    holding_returns: dict[str, pd.Series] | None = None,
    benchmark_returns: pd.Series | None = None,
    holdings: list[Holding] | None = None,
    whitelist: dict | None = None,
) -> dict[str, Any]:
    """Run full Phase 3 prescription pipeline.

    Args:
        diagnosis_data: Bare .data dict from Phase 2 (NOT the full envelope).
            Must pass validate_diagnosis_data() - see diagnosis_schema.py.
        constraints: Parsed user constraints.
        holding_returns: Optional per-holding return series for rescore/stress.
        benchmark_returns: Optional benchmark return series.
        holdings: Optional list of Holding dataclass instances.
        whitelist: Optional asset whitelist override.

    Returns:
        dict with 6 business keys + private _exec_stats.
    """
    from diagnosis_schema import validate_diagnosis_data, DiagnosisSchemaError

    # Schema validation — reject envelopes, require bare data
    if "status" in diagnosis_data and "data" in diagnosis_data:
        raise ValueError(
            "run_prescription() expects bare diagnosis data (the .data dict), "
            "not the full envelope. Strip .data before calling."
        )
    validate_diagnosis_data(diagnosis_data)

    data = diagnosis_data
    flags = data.get("risk_flags", [])
    rm = data.get("risk_metrics", {})
    holdings_metrics = rm.get("holdings", [])
    portfolio_metrics = rm.get("portfolio", {})
    metadata = data.get("metadata", {})
    ann_factor = metadata.get("annualization_factor", 252)
    liquidity = data.get("liquidity")

    original_weights = {h["code"]: h["weight_pct"] / 100 for h in holdings_metrics}
    original_cash_frac = constraints.existing_cash_pct / 100
    original_rescore_metrics = {
        "ann_volatility": portfolio_metrics.get("ann_volatility", 0),
        "max_drawdown": portfolio_metrics.get("max_drawdown", 0),
        "sharpe_ratio": portfolio_metrics.get("sharpe_ratio", 0),
        "max_sector_pct": data.get("concentration", {}).get("max_sector_pct", 0),
        "max_pairwise_corr": _max_corr(data.get("correlation_matrix", {})),
        "hhi": data.get("concentration", {}).get("hhi", 0),
        "factor_dominant_score": _max_factor(data.get("factor_exposure", {}).get("portfolio", {})),
    }

    # _exec_stats accumulator
    exec_stats = {
        "status": "",
        "rescore_attempted": 0,
        "rescore_passed": 0,
        "stress_test_attempted": 0,
        "holding_returns_provided": holding_returns is not None,
    }

    status = classify_entry(flags)
    exec_stats["status"] = status

    if status == "no_action":
        return _finalize_response(_build_response(
            "no_action",
            "当前组合整体处于可接受区间，暂时不需要主动大调仓，建议按既有节奏复查。",
            constraints,
        ), exec_stats)

    inferences = run_all_inferences(data, constraints)
    wl = whitelist or load_whitelist()
    h_list = holdings or []
    alignment = run_asset_alignment(h_list, holdings_metrics, wl, constraints, flags, liquidity)

    if status == "light_touch":
        light_rec = _build_light_touch(inferences)
        return _finalize_response(_build_response(
            "light_touch",
            "组合整体仍稳，但有一两处值得先小步微调或持续观察。",
            constraints,
            tier_1_items=[light_rec] if light_rec else [],
            asset_alignment=alignment,
        ), exec_stats)

    missing_sectors = set(alignment.get("main_theme", {}).get("miss_themes", []))
    candidates = map_inferences_to_recommendations(
        inferences,
        None,
        constraints,
        original_weights,
        missing_sectors,
    )

    for rec in candidates:
        if holding_returns and rec.get("category") != "observe":
            exec_stats["rescore_attempted"] += 1
            rec["rescore"] = rescore_candidate(
                rec,
                original_weights,
                original_cash_frac,
                original_rescore_metrics,
                holding_returns,
                constraints,
                ann_factor,
            )
            if rec["rescore"] and rec["rescore"]["verdict"] != "reject":
                exec_stats["rescore_passed"] += 1
                new_w, _ = build_whatif_portfolio(
                    original_weights,
                    original_cash_frac,
                    rec,
                    constraints,
                )
                meta = build_holdings_meta(
                    holding_returns,
                    benchmark_returns,
                    h_list,
                    liquidity,
                    None,
                )
                rec["rescore"]["stress_test"] = run_stress_test(original_weights, new_w, meta)
                exec_stats["stress_test_attempted"] += 1

    deduped = deduplicate(candidates)
    final = truncate_by_tier(deduped, constraints.objectives)

    if not final:
        exec_stats["status"] = "constrained"
        bottlenecks = _build_constraint_bottlenecks(constraints, candidates, alignment)
        return _finalize_response(_build_response(
            "constrained",
            "当前约束下很难给出既小步、又能明显改善指标的方案，建议先处理约束瓶颈再重算。",
            constraints,
            asset_alignment=alignment,
            constraint_bottlenecks=bottlenecks,
        ), exec_stats)

    groups = build_exclusive_groups(final)
    t1 = [r for r in final if r["tier"] == 1]
    t2 = [r for r in final if r["tier"] == 2]
    t3 = [r for r in final if r["tier"] == 3]

    return _finalize_response(_build_response(
        "actionable",
        "组合存在多项可优化点，以下建议按\u201c先小步、再评估、再决定是否继续\u201d来组织。",
        constraints,
        tier_1_items=t1,
        tier_2_items=t2,
        tier_3_items=t3,
        exclusive_groups=groups,
        asset_alignment=alignment,
    ), exec_stats)


def _build_response(
    status: str,
    message: str,
    constraints: UserConstraints,
    tier_1_items: list | None = None,
    tier_2_items: list | None = None,
    tier_3_items: list | None = None,
    exclusive_groups: list | None = None,
    asset_alignment: dict | None = None,
    constraint_bottlenecks: list | None = None,
) -> dict:
    """Build unified response shape — all statuses have same fields."""
    all_items = (tier_1_items or []) + (tier_2_items or []) + (tier_3_items or [])
    top_action = all_items[0]["action"] if all_items else None

    return {
        "recommendations": {
            "status": status,
            "message": message,
            "constraint_bottlenecks": constraint_bottlenecks or [],
            "tier_1": {
                "label": "零成本操作（内部调仓或使用已有现金，无需额外资金或新账户）",
                "max_items": 2,
                "items": tier_1_items or [],
            },
            "tier_2": {
                "label": "需要额外资金",
                "max_items": 2,
                "items": tier_2_items or [],
            },
            "tier_3": {
                "label": "需要开通新品种/新账户",
                "max_items": 1,
                "items": tier_3_items or [],
            },
        },
        "exclusive_groups": exclusive_groups or [],
        "asset_alignment": asset_alignment or {},
        "constraints_applied": {
            "allowed_markets": constraints.allowed_markets,
            "allowed_exposure": constraints.allowed_exposure,
            "allowed_instruments": constraints.allowed_instruments,
            "account_permissions": constraints.account_permissions,
            "existing_cash_pct": constraints.existing_cash_pct,
            "additional_capital_ratio": constraints.additional_capital_ratio,
            "objectives": constraints.objectives,
        },
        "summary": {
            "total": len(all_items),
            "by_tier": {
                "tier_1": len(tier_1_items or []),
                "tier_2": len(tier_2_items or []),
                "tier_3": len(tier_3_items or []),
            },
            "exclusive_group_count": len(exclusive_groups or []),
            "top_action": top_action,
        },
    }


def _finalize_response(response: dict, exec_stats: dict) -> dict:
    response["client_output"] = build_prescription_client_output(response)
    response["_exec_stats"] = exec_stats
    return response


def _build_light_touch(inferences: list[dict]) -> dict | None:
    if not inferences:
        return None
    inf = inferences[0]
    return {
        "id": "R-001",
        "tier": 1,
        "priority": "low",
        "composite_score": None,
        "source_flags": [inf["signal"]],
        "category": "observe",
        "actionability": "directional",
        "funding_req": "none",
        "access_req": [],
        "action": f"检测到轻微信号：{inf['signal']}，先观察，不急于大动仓位",
        "rationale": "当前还不到必须处理的程度，更适合定期复查并做小步判断。",
        "potential_side_effects": [],
        "assumptions": ["基于历史数据判断，建议未来2-4周继续观察同类指标。"],
        "targets": [],
        "instruments": [],
        "derivative_strategy": None,
        "implementation_plan": {
            "style": "observe",
            "watch_window": "2-4周",
            "guidance": "这条属于观察项，不要求立刻执行调仓。",
        },
        "summary_plain": f"当前主要是轻微信号 {inf['signal']}，先观察再说。",
        "rescore": None,
    }


def _build_constraint_bottlenecks(
    constraints: UserConstraints,
    candidates: list[dict],
    alignment: dict,
) -> list[str]:
    bottlenecks: list[str] = []
    if not candidates:
        bottlenecks.append("当前信号能映射出的候选动作较少，说明约束与候选工具的交集偏窄。")
    if "etf" not in constraints.allowed_instruments:
        bottlenecks.append("当前不允许 ETF，很多更平滑的小步分散方案无法落地。")
    if constraints.existing_cash_pct <= 0 and constraints.additional_capital_ratio == "none":
        bottlenecks.append("当前没有可用现金，也不考虑新增资金，小步试配空间非常有限。")
    defensive_items = alignment.get("defensive", {}).get("items", [])
    missing = [i["asset_name"] for i in defensive_items if i.get("status") in ("missing", "insufficient")]
    if missing:
        bottlenecks.append(f"防守资产仍偏少（{', '.join(missing)}），但现有约束不足以平滑补齐。")
    if not bottlenecks:
        bottlenecks.append("候选动作在回验后改善幅度不稳定，暂不建议为了调仓而调仓。")
    return bottlenecks


def _max_corr(corr_data: dict) -> float:
    pairs = corr_data.get("high_correlation_pairs", [])
    if not pairs:
        return 0.0
    return max(abs(p["correlation"]) for p in pairs)


def _max_factor(portfolio_factors: dict) -> float:
    if not portfolio_factors:
        return 0.0
    return max(abs(v) for v in portfolio_factors.values())

"""Stage B: mapping engine — inference -> instrument selection with gentler sizing."""
from __future__ import annotations

from typing import Any

from data_loader import UserConstraints, CAPITAL_LEVELS
from prescribe.strategy_templates import load_catalog, is_investable


# ---------------------------------------------------------------------------
# Multi-objective scoring
# ---------------------------------------------------------------------------

SINGLE_OBJECTIVE_WEIGHTS = {
    "growth":   {"dividend": 0.5, "low_vol": 0.5, "factor_offset": 1.0, "sector_fill": 1.5, "low_corr": 1.0},
    "income":   {"dividend": 2.0, "low_vol": 1.5, "factor_offset": 0.8, "sector_fill": 0.8, "low_corr": 1.0},
    "hedge":    {"dividend": 0.5, "low_vol": 2.0, "factor_offset": 1.0, "sector_fill": 0.5, "low_corr": 2.0},
    "ipo_base": {"dividend": 0.8, "low_vol": 1.0, "factor_offset": 0.5, "sector_fill": 0.5, "low_corr": 0.5},
}

_DIMS = ["dividend", "low_vol", "factor_offset", "sector_fill", "low_corr"]
_WATCH_WINDOW = "2-4周"
_BASE_SIGNAL_ALLOCATION = {
    "portfolio_vol_high": 0.06,
    "max_dd_deep": 0.05,
    "holding_cap": 0.05,
    "sector_cap": 0.04,
    "low_sharpe": 0.04,
    "risk_contribution_imbalance": 0.05,
}
_SIDE_EFFECTS = {
    "portfolio_vol_high": ["高弹性个股若快速反弹，组合短期收益弹性会下降。"],
    "max_dd_deep": ["若市场快速反弹，先减仓的标的可能出现技术性回升。"],
    "holding_cap": ["单一重仓弹性下降后，超额收益可能不如原来集中持仓时明显。"],
    "low_sharpe": ["把仓位从弱资产转向稳健资产后，进攻性通常会下降。"],
    "risk_contribution_imbalance": ["组合会更稳，但短期主题弹性通常会一起变弱。"],
    "high_correlation": ["短期内组合走势会更分散，不一定每个上涨日都能跟上最强赛道。"],
    "add_asset": ["新资产需要观察和原组合的磨合期，短期表现可能和预期不同。"],
}


def compute_objective_weights(objectives: list[str]) -> dict[str, float]:
    """Multi-select objectives -> blended scoring weights."""
    combined = {d: 0.0 for d in _DIMS}
    valid = [o for o in objectives if o in SINGLE_OBJECTIVE_WEIGHTS]
    if not valid:
        valid = ["growth"]
    for obj in valid:
        w = SINGLE_OBJECTIVE_WEIGHTS[obj]
        for d in _DIMS:
            combined[d] += w[d]
    n = len(valid)
    return {d: combined[d] / n for d in _DIMS}


def score_candidate(
    candidate: dict,
    obj_weights: dict[str, float],
    missing_sectors: set[str],
) -> float:
    """Score a candidate instrument based on objective-weighted criteria."""
    cats = set(candidate.get("category", []))
    sector = candidate.get("sector", "")
    w = obj_weights

    score = (
        w["dividend"] * (1.0 if "dividend" in cats else 0.0) +
        w["low_vol"] * (1.0 if "low_volatility" in cats or "bond" in cats else 0.0) +
        w["low_corr"] * (1.0 if "gold" in cats or "hedge" in cats or "bond" in cats else 0.3) +
        w["sector_fill"] * (1.0 if sector in missing_sectors else 0.0) +
        w["factor_offset"] * 0.5
    )
    return round(score, 4)


# ---------------------------------------------------------------------------
# Funding classification
# ---------------------------------------------------------------------------

def compute_suggested_pct(
    target_allocation: float,
    constraints: UserConstraints,
) -> tuple[float, str]:
    """Return (final_portfolio_weight, funding_req). Three branches."""
    cash_available = constraints.existing_cash_pct / 100

    if target_allocation <= cash_available:
        return target_allocation, "use_cash"

    if constraints.additional_capital_ratio != "none":
        capital_ratio = CAPITAL_LEVELS[constraints.additional_capital_ratio]
        dilution = 1 / (1 + capital_ratio)
        new_capital_space = capital_ratio / (1 + capital_ratio)
        diluted_cash = cash_available * dilution
        total_available = diluted_cash + new_capital_space
        return min(target_allocation, total_available), "new_capital"

    if cash_available > 0.01:
        return cash_available, "use_cash"

    return 0.0, "skip"


# ---------------------------------------------------------------------------
# Tier + actionability assignment
# ---------------------------------------------------------------------------

def assign_display_tier(funding_req: str, access_req: list[str]) -> int:
    """Hardcoded tier: access_req > funding_req."""
    if access_req:
        return 3
    if funding_req == "new_capital":
        return 2
    return 1


def assign_actionability(category: str, has_derivative: bool) -> str:
    """specific = concrete simulation; directional = strategy direction only."""
    if has_derivative:
        return "directional"
    if category == "observe":
        return "directional"
    return "specific"


# ---------------------------------------------------------------------------
# Recommendation planning helpers
# ---------------------------------------------------------------------------

def _normalize_existing_weights(existing_weights: dict[str, float] | set[str]) -> dict[str, float]:
    if isinstance(existing_weights, set):
        return {code: 0.0 for code in existing_weights}
    return existing_weights


def _immediate_target(current_pct: float, target_pct: float) -> float:
    return round(target_pct, 1)


def _build_plan_summary(
    from_pct: float,
    step_target_pct: float,
    full_target_pct: float | None,
    direction: str,
) -> tuple[dict[str, Any], str]:
    full_target = round(full_target_pct if full_target_pct is not None else step_target_pct, 1)
    immediate_change = round(abs(step_target_pct - from_pct), 1)
    full_change = round(abs(full_target - from_pct), 1)
    style = "phased" if abs(full_target - step_target_pct) >= 0.5 else "single_step"

    if direction == "reduce":
        sentence = f"先把仓位从{from_pct:.1f}%调到{step_target_pct:.1f}%"
        follow_up = (
            f"，若{_WATCH_WINDOW}后风险仍偏高，再考虑逐步靠近{full_target:.1f}%"
            if style == "phased" else "，优先看波动和回撤是否同步改善"
        )
    else:
        sentence = f"先把仓位提高到{step_target_pct:.1f}%"
        follow_up = (
            f"，若分散效果符合预期，再逐步提高到{full_target:.1f}%"
            if style == "phased" else "，先用小仓位验证适配度"
        )

    plan = {
        "style": style,
        "watch_window": _WATCH_WINDOW,
        "current_pct": round(from_pct, 1),
        "step_target_pct": round(step_target_pct, 1),
        "full_target_pct": full_target,
        "immediate_change_pct": immediate_change,
        "full_change_pct": full_change,
        "guidance": f"{sentence}{follow_up}。",
    }
    return plan, plan["guidance"]


def _build_side_effects(signal: str, category: str) -> list[str]:
    effects = list(_SIDE_EFFECTS.get(signal, []))
    if category == "add_asset":
        effects.extend(_SIDE_EFFECTS.get("add_asset", []))
    return effects


def _build_assumptions(signal: str, funding_req: str, access_req: list[str]) -> list[str]:
    assumptions = [f"默认先观察{_WATCH_WINDOW}，再决定是否推进到完整目标。"]
    if funding_req == "use_cash":
        assumptions.append("默认优先动用现有现金，不强制同步卖出原有核心资产。")
    elif funding_req == "new_capital":
        assumptions.append("完整执行版假设可按约束补充新资金，并按比例稀释原仓位。")
    if access_req:
        assumptions.append("完整执行前提是相关账户权限已经开通。")
    if signal == "low_sharpe":
        assumptions.append("默认弱资产的问题更多来自风险收益比，而不是短线事件。")
    return assumptions


def _build_plain_summary(action: str, rationale: str, plan_text: str) -> str:
    return f"{action}。{plan_text} 主要原因：{rationale}。"


def _determine_add_allocations(
    signal: str,
    candidate: dict,
    constraints: UserConstraints,
) -> tuple[float, float]:
    full_target = _BASE_SIGNAL_ALLOCATION.get(signal, 0.04)
    cats = set(candidate.get("category", []))

    if "gold" in cats:
        full_target = max(full_target, 0.04)
    elif "bond" in cats:
        full_target = max(full_target, 0.05)
    elif "dividend" in cats and "income" in constraints.objectives:
        full_target = max(full_target, 0.05)
    elif "growth" in cats and "growth" in constraints.objectives:
        full_target = min(full_target + 0.01, 0.06)

    step_target = max(0.03, round(full_target * 0.65, 4))
    return step_target, round(full_target, 4)


# ---------------------------------------------------------------------------
# Main mapping function
# ---------------------------------------------------------------------------

def map_inferences_to_recommendations(
    inferences: list[dict],
    catalog: list[dict] | None,
    constraints: UserConstraints,
    existing_weights: dict[str, float] | set[str],
    missing_sectors: set[str],
) -> list[dict]:
    """Map Stage A inferences to candidate recommendations with instruments."""
    if catalog is None:
        catalog = load_catalog()

    existing_weights_map = _normalize_existing_weights(existing_weights)
    existing_codes = set(existing_weights_map)
    obj_weights = compute_objective_weights(constraints.objectives)
    recommendations = []

    for inf in inferences:
        recommendations.extend(_map_rebalance(inf, constraints, existing_weights_map))
        if inf.get("need_external_addition") or inf.get("need_new_positions"):
            recommendations.extend(
                _map_add_instrument(
                    inf,
                    catalog,
                    constraints,
                    obj_weights,
                    existing_codes,
                    missing_sectors,
                )
            )

    return recommendations


def _map_rebalance(
    inf: dict,
    constraints: UserConstraints,
    existing_weights: dict[str, float],
) -> list[dict]:
    """Generate rebalance/replace recommendations from inference."""
    signal = inf.get("signal", "")
    recs = []

    if signal == "portfolio_vol_high":
        for p in inf.get("problem_holdings", [])[:2]:
            current_pct = p["weight_pct"]
            step_target = _immediate_target(current_pct, p["suggested_new_weight_pct"])
            full_target = round(p.get("full_target_weight_pct", step_target), 1)
            targets = [{
                "code": p["code"],
                "direction": "reduce",
                "from_pct": current_pct,
                "to_pct": step_target,
                "full_target_pct": full_target,
            }]

            released_step = max(current_pct - step_target, 0.0)
            released_full = max(current_pct - full_target, 0.0)
            best_target = next(iter(inf.get("best_internal_targets", [])[:1]), None)
            if best_target and released_step > 0:
                target_from = best_target.get("weight_pct", existing_weights.get(best_target["code"], 0.0) * 100)
                targets.append({
                    "code": best_target["code"],
                    "direction": "increase",
                    "from_pct": round(target_from, 1),
                    "to_pct": round(target_from + released_step * 0.5, 1),
                    "full_target_pct": round(target_from + released_full * 0.7, 1),
                })

            plan, plan_text = _build_plan_summary(current_pct, step_target, full_target, "reduce")
            rationale = (
                f"当前风险贡献{p['risk_contrib_pct']}%，明显高于仓位{current_pct:.1f}%"
                f"（风险放大{p['risk_ratio']}倍）"
            )
            recs.append(_build_rec(
                signal=signal,
                category="rebalance",
                funding_req="none",
                access_req=[],
                action=f"先把{p['name']}({p['code']})从{current_pct:.1f}%降到{step_target:.1f}%",
                rationale=rationale,
                targets=targets,
                implementation_plan=plan,
                summary_plain=_build_plain_summary(
                    f"先降低{p['name']}的单一风险暴露",
                    rationale,
                    plan_text,
                ),
            ))

    elif signal == "max_dd_deep":
        for d in inf.get("drag_holdings", [])[:2]:
            current_pct = d["weight_pct"]
            step_target = _immediate_target(current_pct, d.get("suggested_new_weight_pct", current_pct))
            full_target = round(d.get("full_target_weight_pct", step_target), 1)
            plan, plan_text = _build_plan_summary(current_pct, step_target, full_target, "reduce")
            rec_status = "仍未回到前高" if not d["recovered"] else "虽已恢复但波动仍大"
            rationale = f"该持仓最大回撤{d['dd']:.0%}，对组合回撤拖累约{d['dd_impact']:.1%}，{rec_status}"
            recs.append(_build_rec(
                signal=signal,
                category="rebalance",
                funding_req="none",
                access_req=[],
                action=f"先把{d['name']}({d['code']})从{current_pct:.1f}%降到{step_target:.1f}%",
                rationale=rationale,
                targets=[{
                    "code": d["code"],
                    "direction": "reduce",
                    "from_pct": current_pct,
                    "to_pct": step_target,
                    "full_target_pct": full_target,
                }],
                implementation_plan=plan,
                summary_plain=_build_plain_summary("先降低深回撤资产权重", rationale, plan_text),
            ))

    elif signal == "holding_cap":
        for o in inf.get("overweight_holdings", [])[:2]:
            current_pct = o["weight_pct"]
            step_target = _immediate_target(current_pct, o.get("suggested_new_weight_pct", o["threshold_pct"]))
            full_target = round(o.get("full_target_weight_pct", o["threshold_pct"]), 1)
            plan, plan_text = _build_plan_summary(current_pct, step_target, full_target, "reduce")
            rationale = f"当前超过单一持仓参考上限{o['excess_pct']:.1f}个百分点"
            recs.append(_build_rec(
                signal=signal,
                category="rebalance",
                funding_req="none",
                access_req=[],
                action=f"先把{o['name']}({o['code']})从{current_pct:.1f}%降到{step_target:.1f}%",
                rationale=rationale,
                targets=[{
                    "code": o["code"],
                    "direction": "reduce",
                    "from_pct": current_pct,
                    "to_pct": step_target,
                    "full_target_pct": full_target,
                }],
                implementation_plan=plan,
                summary_plain=_build_plain_summary("先给重仓位降温", rationale, plan_text),
            ))

    elif signal == "low_sharpe":
        worst = inf.get("worst_holdings", [])[:1]
        best = inf.get("best_holdings", [])[:1]
        for w in worst:
            for b in best:
                shift_step = min(max(w.get("weight_pct", 0) * 0.15, 2.0), 4.0)
                shift_full = min(max(w.get("weight_pct", 0) * 0.25, shift_step), 6.0)
                from_w = round(w.get("weight_pct", existing_weights.get(w["code"], 0.0) * 100), 1)
                from_b = round(b.get("weight_pct", existing_weights.get(b["code"], 0.0) * 100), 1)
                to_w = round(max(from_w - shift_step, 0.0), 1)
                full_w = round(max(from_w - shift_full, 0.0), 1)
                to_b = round(from_b + shift_step, 1)
                full_b = round(from_b + shift_full, 1)
                plan, plan_text = _build_plan_summary(from_w, to_w, full_w, "reduce")
                rationale = f"当前问题更像{inf.get('root_cause', 'mixed')}，强弱资产的夏普差距明显"
                recs.append(_build_rec(
                    signal=signal,
                    category="rebalance",
                    funding_req="none",
                    access_req=[],
                    action=f"先从{w['name']}挪出{shift_step:.1f}%仓位，转给{b['name']}",
                    rationale=rationale,
                    targets=[
                        {"code": w["code"], "direction": "reduce", "from_pct": from_w, "to_pct": to_w, "full_target_pct": full_w},
                        {"code": b["code"], "direction": "increase", "from_pct": from_b, "to_pct": to_b, "full_target_pct": full_b},
                    ],
                    implementation_plan=plan,
                    summary_plain=_build_plain_summary("先把仓位从低性价比资产挪到更稳健的持仓", rationale, plan_text),
                ))

    elif signal == "high_correlation":
        for pair in inf.get("correlated_pairs", []):
            rep = pair.get("replace_candidate", {})
            if rep.get("code"):
                from_pct = round(existing_weights.get(rep["code"], 0.0) * 100, 1)
                if from_pct <= 0:
                    continue
                step_target = round(from_pct * 0.75, 1)
                full_target = round(from_pct * 0.55, 1)
                plan, plan_text = _build_plan_summary(from_pct, step_target, full_target, "reduce")
                rationale = f"与保留标的的相关性约为{pair['correlation']:.2f}，分散作用偏弱"
                recs.append(_build_rec(
                    signal=signal,
                    category="replace",
                    funding_req="none",
                    access_req=[],
                    action=f"先把{rep['name']}({rep['code']})降到{step_target:.1f}%，腾出仓位给更低相关资产",
                    rationale=rationale,
                    targets=[{
                        "code": rep["code"],
                        "direction": "reduce",
                        "from_pct": from_pct,
                        "to_pct": step_target,
                        "full_target_pct": full_target,
                    }],
                    implementation_plan=plan,
                    summary_plain=_build_plain_summary("先拆开高度同涨同跌的两只持仓", rationale, plan_text),
                ))

    elif signal == "risk_contribution_imbalance":
        for h in inf.get("imbalanced_holdings", [])[:1]:
            current_pct = h["weight_pct"]
            step_target = _immediate_target(current_pct, h.get("suggested_new_weight_pct", current_pct))
            full_target = round(h.get("full_target_weight_pct", step_target), 1)
            plan, plan_text = _build_plan_summary(current_pct, step_target, full_target, "reduce")
            rationale = f"仓位{current_pct:.1f}%却贡献了{h['risk_contrib_pct']:.1f}%风险，风险放大{h['ratio']}倍"
            recs.append(_build_rec(
                signal=signal,
                category="rebalance",
                funding_req="none",
                access_req=[],
                action=f"先把{h['name']}({h['code']})从{current_pct:.1f}%降到{step_target:.1f}%",
                rationale=rationale,
                targets=[{
                    "code": h["code"],
                    "direction": "reduce",
                    "from_pct": current_pct,
                    "to_pct": step_target,
                    "full_target_pct": full_target,
                }],
                implementation_plan=plan,
                summary_plain=_build_plain_summary("先让风险贡献回到更接近仓位占比的位置", rationale, plan_text),
            ))

    return recs


def _map_add_instrument(
    inf: dict,
    catalog: list[dict],
    constraints: UserConstraints,
    obj_weights: dict[str, float],
    existing_codes: set[str],
    missing_sectors: set[str],
) -> list[dict]:
    """Generate add-instrument recommendations."""
    recs = []
    scored = []
    for c in catalog:
        if c["code"] in existing_codes:
            continue
        ok, missing_perms = is_investable(c, "hold_etf", constraints)
        if not ok:
            continue
        s = score_candidate(c, obj_weights, missing_sectors)
        scored.append((s, c, missing_perms))

    scored.sort(key=lambda x: -x[0])

    for score, c, missing_perms in scored[:2]:
        step_target, full_target = _determine_add_allocations(inf.get("signal", ""), c, constraints)
        suggested, funding_req = compute_suggested_pct(step_target, constraints)
        if funding_req == "skip":
            continue

        full_suggested, _ = compute_suggested_pct(full_target, constraints)
        access_req = missing_perms
        plan, plan_text = _build_plan_summary(0.0, suggested * 100, full_suggested * 100, "increase")
        rationale = f"候选评分{score:.2f}，用于补充组合多样性和缓冲资产"
        recs.append(_build_rec(
            signal=inf["signal"],
            category="add_asset",
            funding_req=funding_req,
            access_req=access_req,
            action=f"先试配{c['name']}({c['code']}) {suggested*100:.1f}%",
            rationale=rationale,
            instruments=[{
                "code": c["code"],
                "name": c["name"],
                "current_pct": 0.0,
                "suggested_pct": round(suggested * 100, 1),
                "full_target_pct": round(full_suggested * 100, 1),
                "runtime_stats": None,
            }],
            implementation_plan=plan,
            summary_plain=_build_plain_summary("先用小仓位测试新资产的分散效果", rationale, plan_text),
        ))

    return recs


def _build_rec(
    signal: str,
    category: str,
    funding_req: str,
    access_req: list[str],
    action: str,
    rationale: str,
    targets: list[dict] | None = None,
    instruments: list[dict] | None = None,
    implementation_plan: dict[str, Any] | None = None,
    summary_plain: str | None = None,
) -> dict:
    """Build a recommendation dict with all required fields."""
    tier = assign_display_tier(funding_req, access_req)
    actionability = assign_actionability(category, False)

    return {
        "id": None,
        "tier": tier,
        "priority": "high" if tier == 1 else ("medium" if tier == 2 else "low"),
        "composite_score": None,
        "source_flags": [signal],
        "category": category,
        "actionability": actionability,
        "funding_req": funding_req,
        "access_req": access_req,
        "action": action,
        "rationale": rationale,
        "potential_side_effects": _build_side_effects(signal, category),
        "assumptions": _build_assumptions(signal, funding_req, access_req),
        "targets": targets or [],
        "instruments": instruments or [],
        "derivative_strategy": None,
        "implementation_plan": implementation_plan or {},
        "summary_plain": summary_plain or action,
        "rescore": None,
    }

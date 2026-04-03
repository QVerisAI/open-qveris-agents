"""Structured client-facing output builders for diagnosis and prescription results."""
from __future__ import annotations

from typing import Any


PERCENT_METRICS = {
    "ann_volatility",
    "max_drawdown",
    "max_sector_pct",
    "alpha_annual",
    "beta",
}

VALUE_LABELS = {
    "a-share": "A股",
    "a_share": "A股",
    "a股": "A股",
    "global": "全球",
    "hk_connect": "港股通",
    "hk-stock": "港股",
    "hk_stock": "港股",
    "us-stock": "美股",
    "us_stock": "美股",
    "crypto": "加密资产",
    "stock": "个股",
    "etf": "ETF",
    "fund": "基金",
    "bond": "债券",
    "cash": "现金",
    "futures": "期货",
    "options": "期权",
    "growth": "资产增值",
    "income": "稳定现金流",
    "hedge": "对冲已有风险",
    "capital_growth": "资产增值",
    "stable_cashflow": "稳定现金流",
    "hedge_existing_risk": "对冲已有风险",
    "ipo_ballast": "打新底仓",
    "none": "无新增资金",
}


def build_diagnosis_client_output(result: dict[str, Any]) -> dict[str, Any]:
    """Build structured Chinese text from a Phase 2 diagnosis result."""
    data = result.get("data") or result
    portfolio = data.get("risk_metrics", {}).get("portfolio", {})
    concentration = data.get("concentration", {})
    benchmark = data.get("benchmark", {})
    flags = data.get("risk_flags", [])
    sectors = data.get("sector_exposure", {})
    rc_holdings = data.get("risk_contribution", {}).get("by_holding", [])
    corr_pairs = data.get("correlation_matrix", {}).get("high_correlation_pairs", [])

    high_count = sum(1 for item in flags if item.get("severity") == "high")
    medium_count = sum(1 for item in flags if item.get("severity") == "medium")

    if flags:
        headline = (
            f"当前组合识别出 {len(flags)} 项风险提示，其中高优先级 {high_count} 项、关注项 {medium_count} 项，"
            "更适合先解释风险结构，再决定是否进入优化阶段。"
        )
    else:
        headline = "当前组合主要指标处于可接受区间，暂未发现必须立刻处理的明显风险点。"

    top_sector = max(sectors.items(), key=lambda item: item[1])[0] if sectors else None
    top_sector_weight = sectors.get(top_sector, 0.0) if top_sector else None
    top_risk = max(rc_holdings, key=lambda item: item.get("pct_risk_contribution", 0.0)) if rc_holdings else None

    sections = [
        {
            "heading": "怎么读这份诊断",
            "bullets": [
                "先看总体判断，确认这更像是“短期需要处理的风险”，还是“可以继续观察的偏差”。",
                "再看关键指标和风险提示，重点解释波动、回撤、集中度和相对基准表现。",
                "如果客户只想知道现状，到这里就可以结束；如果要继续优化，再进入 Phase 3。",
            ],
        },
        {
            "heading": "总体判断",
            "bullets": [
                headline,
                (
                    f"组合年化波动约 {_fmt_pct(portfolio.get('ann_volatility'))}，"
                    f"最大回撤约 {_fmt_pct(portfolio.get('max_drawdown'))}，"
                    f"夏普比率约 {_fmt_num(portfolio.get('sharpe_ratio'))}。"
                ),
            ],
        },
        {
            "heading": "客户现在最该知道的事",
            "bullets": _compact(
                [
                    (
                        f"单一持仓最高约 {_fmt_pct(concentration.get('max_holding_pct'))}，"
                        "如果明显偏高，后续解释重点应放在“单一标的拖累整个账户”的风险。"
                        if concentration.get("max_holding_pct") is not None else None
                    ),
                    (
                        f"当前最大行业暴露在「{top_sector}」，约占非现金资产 {_fmt_pct(top_sector_weight)}。"
                        if top_sector and top_sector_weight is not None else None
                    ),
                    (
                        f"风险贡献最高的持仓是 {top_risk.get('code')}，约贡献总波动的 {_fmt_pct(top_risk.get('pct_risk_contribution'))}。"
                        if top_risk else None
                    ),
                    (
                        f"与基准相比，当前 alpha 约 {_fmt_pct(benchmark.get('alpha_annual'))}，beta 约 {_fmt_num(benchmark.get('beta'))}。"
                        if benchmark else None
                    ),
                ]
            ),
        },
        {
            "heading": "主要风险提示",
            "bullets": (
                [
                    (
                        f"{item.get('metric_cn') or item.get('metric')}：{item.get('explanation')} "
                        f"(实际值 {_render_scalar(item.get('actual_value'))}，阈值 {_render_scalar(item.get('threshold'))})"
                    )
                    for item in flags
                ] if flags else ["当前没有触发明确的风险标记。"]
            ),
        },
        {
            "heading": "给客户的下一步说法",
            "bullets": [
                (
                    "如果客户只想先了解现状，可以停留在这一版诊断结论，不需要继续生成优化方案。"
                    if flags else
                    "如果客户当前目标只是复查健康度，这一版结果已经足够，不必为了轻微波动频繁调整。"
                ),
                (
                    "如果客户愿意继续，就进入优化阶段，把“能投什么、还能加多少资金、希望优先解决什么问题”补齐。"
                    if flags else
                    "如果后续要做优化，可以在明确约束后再进入 Phase 3。"
                ),
            ],
        },
    ]

    tables = [
        _build_table(
            "关键指标",
            ["指标", "当前值"],
            [
                ["年化波动", _fmt_pct(portfolio.get("ann_volatility"))],
                ["最大回撤", _fmt_pct(portfolio.get("max_drawdown"))],
                ["夏普比率", _fmt_num(portfolio.get("sharpe_ratio"))],
                ["单一持仓上限", _fmt_pct(concentration.get("max_holding_pct"))],
                ["行业集中度上限", _fmt_pct(concentration.get("max_sector_pct"))],
                ["有效持仓数", _fmt_num(concentration.get("effective_n"))],
                ["Alpha", _fmt_pct(benchmark.get("alpha_annual"))],
                ["Beta", _fmt_num(benchmark.get("beta"))],
            ],
        )
    ]

    if flags:
        tables.append(
            _build_table(
                "风险提示明细",
                ["严重程度", "指标", "实际值", "阈值", "说明"],
                [
                    [
                        _severity_label(item.get("severity")),
                        item.get("metric_cn") or item.get("metric") or "—",
                        _render_scalar(item.get("actual_value")),
                        _render_scalar(item.get("threshold")),
                        item.get("explanation") or "—",
                    ]
                    for item in flags
                ],
            )
        )

    if rc_holdings:
        tables.append(
            _build_table(
                "风险贡献前列持仓",
                ["代码", "风险贡献占比", "归一化权重"],
                [
                    [
                        item.get("code") or "—",
                        _fmt_pct(item.get("pct_risk_contribution")),
                        _fmt_pct(item.get("weight_normalized")),
                    ]
                    for item in rc_holdings[:5]
                ],
            )
        )

    if corr_pairs:
        tables.append(
            _build_table(
                "高相关持仓配对",
                ["配对", "相关系数"],
                [
                    [
                        f"{item.get('code_a', '—')} / {item.get('code_b', '—')}",
                        _fmt_num(item.get("correlation")),
                    ]
                    for item in corr_pairs[:5]
                ],
            )
        )

    return _build_client_output("组合诊断摘要", headline, sections, tables=tables)


def build_prescription_client_output(result: dict[str, Any]) -> dict[str, Any]:
    """Build structured Chinese text from a Phase 3 prescription result."""
    recs = result.get("recommendations", {})
    summary = result.get("summary", {})
    alignment = result.get("asset_alignment", {})
    constraints = result.get("constraints_applied", {})
    exclusive = result.get("exclusive_groups", [])
    bottlenecks = recs.get("constraint_bottlenecks", [])
    tier_1 = recs.get("tier_1", {}).get("items", [])
    tier_2 = recs.get("tier_2", {}).get("items", [])
    tier_3 = recs.get("tier_3", {}).get("items", [])
    all_items = tier_1 + tier_2 + tier_3

    headline = recs.get("message") or "当前暂无可执行建议。"

    sections = [
        {
            "heading": "怎么读这份建议",
            "bullets": [
                "先看总体判断，确认这次输出是“可以动手的小步优化”，还是“约束太强暂时不建议动”。",
                "再看每条建议卡片：先读给小白的解释，再看前后对比表和两档模拟，最后再看副作用与前提假设。",
                "如果客户只愿意先试一步，就只执行“先做一步”的版本；不要默认一次做到完整目标。 ",
            ],
        },
        {
            "heading": "总体判断",
            "bullets": [
                headline,
                (
                    f"当前共形成 {summary.get('total', 0)} 条建议，"
                    f"其中 Tier 1 / 2 / 3 分别为 "
                    f"{summary.get('by_tier', {}).get('tier_1', 0)} / "
                    f"{summary.get('by_tier', {}).get('tier_2', 0)} / "
                    f"{summary.get('by_tier', {}).get('tier_3', 0)} 条。"
                ),
                f"当前优先动作：{summary.get('top_action') or '暂无'}。",
            ],
        },
        {
            "heading": "执行瓶颈",
            "bullets": bottlenecks or ["当前没有明显的执行瓶颈，重点是控制调整节奏，不要一步做太大。"],
        },
        {
            "heading": "优先动作",
            "bullets": _recommendation_lines(tier_1) or ["当前没有需要立刻推进的 Tier 1 动作。"],
        },
        {
            "heading": "备选动作",
            "bullets": _recommendation_lines(tier_2 + tier_3) or ["当前没有需要额外资金或新权限的备选动作。"],
        },
        {
            "heading": "资产对齐与防守检查",
            "bullets": _alignment_bullets(alignment),
        },
        {
            "heading": "执行前提与提醒",
            "bullets": _compact(
                bottlenecks + _collect_item_notes(all_items)
            ) or ["当前暂无额外执行提醒。"],
        },
    ]

    tables = [_constraints_table(constraints)]
    defensive_table = _defensive_table(alignment)
    if defensive_table["rows"]:
        tables.append(defensive_table)
    if exclusive:
        tables.append(
            _build_table(
                "互斥关系",
                ["组别", "建议", "原因"],
                [
                    [
                        item.get("group_id") or "—",
                        " / ".join(item.get("recommendation_ids", [])) or "—",
                        item.get("reason") or "—",
                    ]
                    for item in exclusive
                ],
            )
        )

    recommendation_cards = [_recommendation_card(item) for item in all_items]

    return _build_client_output(
        "组合优化建议",
        headline,
        sections,
        tables=tables,
        recommendation_cards=recommendation_cards,
    )


def _alignment_bullets(alignment: dict[str, Any]) -> list[str]:
    main = alignment.get("main_theme", {})
    defensive = alignment.get("defensive", {})
    unclassified = alignment.get("unclassified", {})
    missing = [
        item.get("asset_name")
        for item in defensive.get("items", [])
        if item.get("status") in {"missing", "insufficient"}
    ]
    return _compact(
        [
            (
                f"主赛道覆盖约 {_fmt_pct(main.get('theme_weight_coverage'))}，"
                f"命中的主线包括：{_join_names(main.get('hit_themes', []), key='name') or '暂无'}。"
            ),
            (
                f"仍缺少的赛道方向：{' / '.join(main.get('miss_themes', []))}。"
                if main.get("miss_themes") else None
            ),
            (
                f"当前防守资产缺口主要在：{' / '.join(missing)}。"
                if missing else "当前防守资产配置没有明显缺口。"
            ),
            (
                f"非白名单资产占比约 {_fmt_pct(unclassified.get('unclassified_pct'))}，"
                "这部分后续需要更主动判断是否继续保留。"
                if unclassified.get("unclassified_pct") is not None else None
            ),
        ]
    )


def _constraints_table(constraints: dict[str, Any]) -> dict[str, Any]:
    return _build_table(
        "约束条件",
        ["项目", "内容"],
        [
            ["可投资市场", _localize_join(constraints.get("allowed_markets", []), default="未设置")],
            ["可接受敞口", _localize_join(constraints.get("allowed_exposure", []), default="未设置")],
            ["工具类型", _localize_join(constraints.get("allowed_instruments", []), default="未设置")],
            ["已开通权限", _localize_join(constraints.get("account_permissions", []), default="无")],
            ["现有现金", _fmt_pct(_to_ratio(constraints.get("existing_cash_pct")))],
            ["可追加资金", _localize_scalar(constraints.get("additional_capital_ratio"), default="无新增资金")],
            ["优化目标", _localize_join(constraints.get("objectives", []), default="未设置")],
        ],
    )


def _defensive_table(alignment: dict[str, Any]) -> dict[str, Any]:
    items = alignment.get("defensive", {}).get("items", [])
    rows = [
        [
            item.get("asset_name") or "—",
            _defensive_status_label(item.get("status")),
            _fmt_pct(_to_ratio(item.get("current_pct"))),
            _fmt_range(item.get("suggested_range_pct")),
            _fmt_range(item.get("feasible_range_pct")),
            "、".join(item.get("trigger_reasons", [])) or "—",
        ]
        for item in items
    ]
    return _build_table(
        "防守资产检查",
        ["资产", "当前判断", "现有比例", "建议区间", "按约束可达", "触发原因"],
        rows,
    )


def _recommendation_card(item: dict[str, Any]) -> dict[str, Any]:
    rescore = item.get("rescore") or {}
    plan = item.get("implementation_plan") or {}
    simulation_rows = [
        [
            case.get("name") or "—",
            case.get("summary") or "—",
            _fmt_pct(case.get("estimated_turnover_cost"), digits=2),
        ]
        for case in rescore.get("simulation_cases", [])
    ]
    metrics_rows = [
        [
            _metric_label(metric),
            _format_metric(metric, rescore.get("original", {}).get(metric)),
            _format_metric(metric, rescore.get("whatif", {}).get(metric)),
            _format_metric_delta(metric, rescore.get("delta", {}).get(metric)),
        ]
        for metric in (
            "ann_volatility",
            "max_drawdown",
            "sharpe_ratio",
            "max_sector_pct",
            "max_pairwise_corr",
            "hhi",
            "factor_dominant_score",
        )
        if any(
            value is not None
            for value in (
                rescore.get("original", {}).get(metric),
                rescore.get("whatif", {}).get(metric),
                rescore.get("delta", {}).get(metric),
            )
        )
    ]
    stress_rows = [
        [
            row.get("scenario_name") or row.get("scenario") or "—",
            _fmt_pct(row.get("original_loss")),
            _fmt_pct(row.get("whatif_loss")),
            _stress_verdict_label(row.get("verdict")),
        ]
        for row in rescore.get("stress_test", [])
    ]

    return {
        "id": item.get("id") or "—",
        "tier": item.get("tier"),
        "tier_label": f"Tier {item.get('tier', '—')}",
        "action": item.get("action") or "—",
        "verdict": rescore.get("verdict") or "not_scored",
        "verdict_label": _verdict_label(rescore.get("verdict")),
        "summary_plain": item.get("summary_plain") or item.get("action") or "—",
        "beginner_summary": rescore.get("beginner_summary") or "当前暂无足够数据做小白版解释。",
        "professional_summary": rescore.get("headline") or "当前暂无回验摘要。",
        "rationale": item.get("rationale") or "—",
        "implementation_guidance": plan.get("guidance") or "暂无分步说明。",
        "targets": _target_lines(item.get("targets", [])),
        "instruments": _instrument_lines(item.get("instruments", [])),
        "side_effects": item.get("potential_side_effects", []) or ["暂无明显额外提示。"],
        "assumptions": item.get("assumptions", []) or ["无。"],
        "simulation_table": _build_table(
            "两档模拟",
            ["模拟版本", "摘要", "交易成本"],
            simulation_rows,
        ),
        "metrics_table": _build_table(
            "前后对比",
            ["指标", "原组合", "先做一步后", "变化"],
            metrics_rows,
        ),
        "stress_table": _build_table(
            "压力测试",
            ["场景", "原组合", "先做一步后", "结论"],
            stress_rows,
        ),
    }


def _recommendation_lines(items: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for item in items:
        parts = [str(item.get("action") or "").strip()]
        summary_plain = str(item.get("summary_plain") or "").strip()
        if summary_plain and summary_plain != parts[0]:
            parts.append(summary_plain)
        headline = str((item.get("rescore") or {}).get("headline") or "").strip()
        if headline:
            parts.append(f"回验摘要：{headline}")
        line = " ".join(part for part in parts if part)
        if line:
            lines.append(line)
    return lines


def _collect_item_notes(items: list[dict[str, Any]]) -> list[str]:
    notes: list[str] = []
    seen: set[str] = set()
    for item in items:
        for text in item.get("potential_side_effects", []):
            content = str(text).strip()
            if content and content not in seen:
                notes.append(f"副作用提醒：{content}")
                seen.add(content)
        for text in item.get("assumptions", []):
            content = str(text).strip()
            if content and content not in seen:
                notes.append(f"前提假设：{content}")
                seen.add(content)
    return notes


def _target_lines(targets: list[dict[str, Any]]) -> list[str]:
    lines = []
    for target in targets:
        full_target = target.get("full_target_pct")
        extra = f"（完整目标 {full_target:.1f}%）" if full_target is not None else ""
        lines.append(
            f"{target.get('code', '—')}：{target.get('from_pct', 0):.1f}% -> {target.get('to_pct', 0):.1f}%{extra}"
        )
    return lines


def _instrument_lines(instruments: list[dict[str, Any]]) -> list[str]:
    lines = []
    for inst in instruments:
        full_target = inst.get("full_target_pct")
        extra = f"，完整目标 {full_target:.1f}%" if full_target is not None else ""
        lines.append(
            f"{inst.get('name', '—')}({inst.get('code', '—')})：先试配 {inst.get('suggested_pct', 0):.1f}%{extra}"
        )
    return lines


def _build_client_output(
    title: str,
    headline: str,
    sections: list[dict[str, Any]],
    *,
    tables: list[dict[str, Any]] | None = None,
    recommendation_cards: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    normalized_sections = []
    for section in sections:
        bullets = _compact(section.get("bullets", []))
        if not bullets:
            continue
        normalized_sections.append({"heading": section["heading"], "bullets": bullets})

    normalized_tables = [table for table in tables or [] if table.get("rows")]
    cards = recommendation_cards or []

    markdown_lines = [f"# {title}", "", headline]
    for section in normalized_sections:
        markdown_lines.extend(["", f"## {section['heading']}"])
        markdown_lines.extend(f"- {bullet}" for bullet in section["bullets"])

    for table in normalized_tables:
        markdown_lines.extend(["", f"## {table['title']}"])
        markdown_lines.extend(_markdown_table(table["columns"], table["rows"]))

    if cards:
        markdown_lines.extend(["", "## 建议卡片"])
    for card in cards:
        markdown_lines.extend(
            [
                "",
                f"### {card['id']} {card['tier_label']} {card['verdict_label']}",
                f"- 动作：{card['action']}",
                f"- 一句话：{card['summary_plain']}",
                f"- 给小白的解释：{card['beginner_summary']}",
                f"- 专业摘要：{card['professional_summary']}",
                f"- 为什么做：{card['rationale']}",
                f"- 执行方式：{card['implementation_guidance']}",
            ]
        )
        if card["targets"]:
            markdown_lines.append("- 调整目标：")
            markdown_lines.extend(f"  - {text}" for text in card["targets"])
        if card["instruments"]:
            markdown_lines.append("- 涉及工具：")
            markdown_lines.extend(f"  - {text}" for text in card["instruments"])
        if card["side_effects"]:
            markdown_lines.append("- 可能副作用：")
            markdown_lines.extend(f"  - {text}" for text in card["side_effects"])
        if card["assumptions"]:
            markdown_lines.append("- 前提假设：")
            markdown_lines.extend(f"  - {text}" for text in card["assumptions"])
        for table_key in ("simulation_table", "metrics_table", "stress_table"):
            table = card[table_key]
            if table["rows"]:
                markdown_lines.extend(["", f"#### {table['title']}"])
                markdown_lines.extend(_markdown_table(table["columns"], table["rows"]))

    return {
        "title": title,
        "headline": headline,
        "sections": normalized_sections,
        "tables": normalized_tables,
        "recommendation_cards": cards,
        "markdown": "\n".join(markdown_lines).strip(),
    }


def _markdown_table(columns: list[str], rows: list[list[str]]) -> list[str]:
    if not rows:
        return []
    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join("---" for _ in columns) + " |"
    body = ["| " + " | ".join(_escape_markdown_cell(cell) for cell in row) + " |" for row in rows]
    return [header, separator, *body]


def _escape_markdown_cell(value: str) -> str:
    return str(value).replace("\n", "<br>").replace("|", "\\|")


def _build_table(title: str, columns: list[str], rows: list[list[Any]]) -> dict[str, Any]:
    return {
        "title": title,
        "columns": columns,
        "rows": [[_render_scalar(cell) for cell in row] for row in rows if any(cell not in (None, "", []) for cell in row)],
    }


def _render_scalar(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return _fmt_num(value)
    return str(value).strip() or "—"


def _localize_join(values: list[Any] | tuple[Any, ...] | None, *, default: str) -> str:
    if not values:
        return default
    localized = [_localize_scalar(value) for value in values]
    return " / ".join(item for item in localized if item) or default


def _localize_scalar(value: Any, *, default: str | None = None) -> str:
    if value is None:
        return default or "—"
    text = str(value).strip()
    if not text:
        return default or "—"
    normalized = text.lower().replace(" ", "_")
    return VALUE_LABELS.get(normalized, text)


def _compact(values: list[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value).strip() if value is not None else ""
        if text:
            result.append(text)
    return result


def _join_names(items: list[dict[str, Any]], *, key: str) -> str:
    return " / ".join(str(item.get(key)).strip() for item in items if str(item.get(key)).strip())


def _severity_label(severity: str | None) -> str:
    mapping = {"high": "高", "medium": "中", "low": "低"}
    return mapping.get((severity or "").lower(), severity or "—")


def _verdict_label(verdict: str | None) -> str:
    mapping = {
        "pass": "通过",
        "pass_with_warning": "有条件通过",
        "reject": "暂不建议",
        "not_scored": "未评估",
    }
    return mapping.get((verdict or "").lower(), verdict or "未评估")


def _defensive_status_label(status: str | None) -> str:
    mapping = {
        "not_triggered": "暂未触发",
        "adequate": "已基本够用",
        "missing": "当前缺口较明显",
        "insufficient": "已有但仍偏少",
    }
    return mapping.get((status or "").lower(), status or "—")


def _stress_verdict_label(verdict: str | None) -> str:
    mapping = {"better": "更稳", "worse": "更弱"}
    return mapping.get((verdict or "").lower(), verdict or "持平")


def _metric_label(metric: str) -> str:
    return {
        "ann_volatility": "年化波动",
        "max_drawdown": "最大回撤",
        "sharpe_ratio": "夏普比率",
        "max_sector_pct": "最大行业暴露",
        "max_pairwise_corr": "最高持仓相关性",
        "hhi": "集中度 HHI",
        "factor_dominant_score": "因子偏移度",
    }.get(metric, metric)


def _format_metric(metric: str, value: float | None) -> str:
    if metric in {"ann_volatility", "max_drawdown", "max_sector_pct"}:
        return _fmt_pct(value)
    return _fmt_num(value)


def _format_metric_delta(metric: str, value: float | None) -> str:
    if value is None:
        return "—"
    if metric in {"ann_volatility", "max_drawdown", "max_sector_pct"}:
        sign = "+" if value > 0 else ""
        return f"{sign}{value * 100:.1f}pct"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}"


def _fmt_pct(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.{digits}f}%"


def _fmt_num(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}"


def _fmt_range(values: list[Any] | tuple[Any, Any] | None) -> str:
    if not values or len(values) < 2:
        return "—"
    return f"{values[0]}% - {values[1]}%"


def _to_ratio(value: float | int | None) -> float | None:
    if value is None:
        return None
    numeric = float(value)
    return numeric / 100 if numeric > 1 else numeric

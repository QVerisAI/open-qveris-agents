"""Position style post-processing for risk flags."""
from __future__ import annotations

from data_loader import Holding


def adjust_flags(
    flags: list[dict],
    position_style: str,
    holdings: list[Holding],
    warnings: list[str],
) -> list[dict]:
    """Apply position-style adjustments to the flag list."""

    if position_style == "timing_rotation":
        # Suppress holding_cap (high concentration is normal for timing)
        flags = [f for f in flags if f["metric"] != "holding_cap"]
        # High cash is normal — no special flag to remove, just note
        warnings.append("timing_rotation: 高集中度和高现金仓位为策略正常特征")

    elif position_style == "full_rotation":
        # Escalate holding_cap to high severity
        for f in flags:
            if f["metric"] == "holding_cap":
                f["severity"] = "high"

    elif position_style == "core_satellite":
        # Heuristic: if growth-role holdings > 50%, add warning
        growth_pct = sum(
            h.weight_pct / 100 for h in holdings
            if not h.is_cash and h.risk_role == "growth"
        )
        if growth_pct > 0.50:
            warnings.append(
                f"core_satellite: 卫星部分(risk_role=growth)占比{growth_pct:.0%}，"
                f"建议增加核心配置"
            )

    # constant_mix and dca: no special adjustments

    return flags

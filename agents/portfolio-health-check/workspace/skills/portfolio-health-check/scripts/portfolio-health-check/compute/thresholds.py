"""
Threshold configuration for risk flags and input validation.

Sources:
- 5 quantitative flags: quantitative-spec.md (authoritative per output-contract.md)
- 2 concentration flags: portfolio-health-check-flow.md
- Horizon multiplier: plan section 六
- Legal param combos: plan section 一
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------------------
# 7-flag x 4-tier threshold table
# None = never triggers (very_aggressive unlimited)
# ---------------------------------------------------------------------------

RISK_THRESHOLDS: dict[str, dict[str, Optional[float]]] = {
    "conservative": {
        "portfolio_vol_high": 0.15,
        "max_dd_deep": 0.10,
        "low_sharpe": 0.5,
        "high_correlation": 0.7,
        "factor_dominance": 0.6,
        "sector_cap": 0.20,
        "holding_cap": 0.15,
    },
    "moderate": {
        "portfolio_vol_high": 0.20,
        "max_dd_deep": 0.15,
        "low_sharpe": 0.5,
        "high_correlation": 0.7,
        "factor_dominance": 0.6,
        "sector_cap": 0.30,
        "holding_cap": 0.25,
    },
    "aggressive": {
        "portfolio_vol_high": 0.30,
        "max_dd_deep": 0.25,
        "low_sharpe": 0.3,
        "high_correlation": 0.85,
        "factor_dominance": 0.8,
        "sector_cap": 0.40,
        "holding_cap": 0.35,
    },
    "very_aggressive": {
        "portfolio_vol_high": None,
        "max_dd_deep": 0.35,
        "low_sharpe": 0.3,
        "high_correlation": 0.85,
        "factor_dominance": 0.8,
        "sector_cap": None,
        "holding_cap": None,
    },
}

# ---------------------------------------------------------------------------
# Investment horizon -> vol/dd threshold multiplier
# Only affects portfolio_vol_high and max_dd_deep
# ---------------------------------------------------------------------------

HORIZON_MULTIPLIER: dict[str, float] = {
    "<1y": 0.8,
    "1-3y": 1.0,
    "3-5y": 1.2,
    ">5y": 1.5,
}

# ---------------------------------------------------------------------------
# Legal parameter combinations
# ---------------------------------------------------------------------------

# (position_style, trading_frequency) -> True/False/"degrade"
_VALID_STYLE_FREQ: dict[tuple[str, str], bool | str] = {}
_STYLES_ALL_FREQ = ["timing_rotation"]
_STYLES_PERIODIC = ["full_rotation", "dca", "core_satellite"]
_STYLES_NO_BUY_HOLD = ["constant_mix"]
_ALL_TRADING_FREQS = ["intraday", "weekly", "monthly", "quarterly", "buy_and_hold"]

for s in _STYLES_ALL_FREQ:
    for tf in _ALL_TRADING_FREQS:
        _VALID_STYLE_FREQ[(s, tf)] = True

for s in _STYLES_PERIODIC:
    for tf in ["weekly", "monthly", "quarterly"]:
        _VALID_STYLE_FREQ[(s, tf)] = True
    _VALID_STYLE_FREQ[(s, "intraday")] = False

# buy_and_hold: dca is contradictory, full_rotation/core_satellite degrade
_VALID_STYLE_FREQ[("dca", "buy_and_hold")] = False
_VALID_STYLE_FREQ[("full_rotation", "buy_and_hold")] = "degrade"
_VALID_STYLE_FREQ[("core_satellite", "buy_and_hold")] = "degrade"

for s in _STYLES_NO_BUY_HOLD:
    for tf in ["intraday", "weekly", "monthly", "quarterly"]:
        _VALID_STYLE_FREQ[(s, tf)] = True
    _VALID_STYLE_FREQ[(s, "buy_and_hold")] = False

# (data_frequency, trading_frequency) -> True/False
_VALID_DATA_FREQ: dict[tuple[str, str], bool] = {}
for tf in _ALL_TRADING_FREQS:
    _VALID_DATA_FREQ[("daily", tf)] = True

for tf in ["weekly", "monthly", "quarterly", "buy_and_hold"]:
    _VALID_DATA_FREQ[("weekly", tf)] = True
_VALID_DATA_FREQ[("weekly", "intraday")] = False

_VALID_DATA_FREQ[("15min", "intraday")] = True
for tf in ["weekly", "monthly", "quarterly", "buy_and_hold"]:
    _VALID_DATA_FREQ[("15min", tf)] = False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_thresholds(risk_tolerance: str) -> dict[str, Optional[float]]:
    """Return base threshold dict for a risk tolerance level."""
    if risk_tolerance not in RISK_THRESHOLDS:
        raise ValueError(
            f"Unknown risk_tolerance: {risk_tolerance}. "
            f"Must be one of {list(RISK_THRESHOLDS)}"
        )
    return dict(RISK_THRESHOLDS[risk_tolerance])


def apply_horizon_adjustment(
    thresholds: dict[str, Optional[float]],
    investment_horizon: str,
) -> dict[str, Optional[float]]:
    """Apply investment horizon multiplier to vol and dd thresholds only."""
    if investment_horizon not in HORIZON_MULTIPLIER:
        raise ValueError(
            f"Unknown investment_horizon: {investment_horizon}. "
            f"Must be one of {list(HORIZON_MULTIPLIER)}"
        )
    multiplier = HORIZON_MULTIPLIER[investment_horizon]
    adjusted = dict(thresholds)
    for key in ("portfolio_vol_high", "max_dd_deep"):
        if adjusted[key] is not None:
            adjusted[key] = adjusted[key] * multiplier
    return adjusted


@dataclass
class ValidationResult:
    valid: bool
    error: Optional[str] = None
    warnings: list[str] | None = None


def validate_params(
    position_style: str,
    trading_frequency: str,
    data_frequency: str,
    risk_tolerance: str,
    investment_horizon: str,
) -> ValidationResult:
    """Validate parameter combinations. Returns ValidationResult."""
    warnings: list[str] = []

    # Check risk_tolerance
    if risk_tolerance not in RISK_THRESHOLDS:
        return ValidationResult(
            False,
            f"Unknown risk_tolerance: {risk_tolerance}",
        )

    # Check investment_horizon
    if investment_horizon not in HORIZON_MULTIPLIER:
        return ValidationResult(
            False,
            f"Unknown investment_horizon: {investment_horizon}",
        )

    # Check data_frequency
    if data_frequency not in ("daily", "weekly", "15min"):
        return ValidationResult(
            False,
            f"Unknown data_frequency: {data_frequency}. Must be daily|weekly|15min",
        )

    # Check (position_style, trading_frequency)
    style_freq = _VALID_STYLE_FREQ.get((position_style, trading_frequency))
    if style_freq is None:
        return ValidationResult(
            False,
            f"Unknown position_style or trading_frequency: "
            f"({position_style}, {trading_frequency})",
        )
    if style_freq is False:
        return ValidationResult(
            False,
            f"Illegal combination: position_style={position_style} "
            f"+ trading_frequency={trading_frequency}",
        )
    if style_freq == "degrade":
        warnings.append(
            f"trading_frequency=buy_and_hold with {position_style} "
            f"is not fully compatible; degrading to drift mode"
        )

    # Check (data_frequency, trading_frequency)
    data_freq = _VALID_DATA_FREQ.get((data_frequency, trading_frequency))
    if data_freq is None:
        return ValidationResult(
            False,
            f"Unknown (data_frequency, trading_frequency): "
            f"({data_frequency}, {trading_frequency})",
        )
    if data_freq is False:
        return ValidationResult(
            False,
            f"Illegal combination: data_frequency={data_frequency} "
            f"+ trading_frequency={trading_frequency}",
        )

    return ValidationResult(True, warnings=warnings if warnings else None)

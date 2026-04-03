"""
Returns computation module.

- Simple returns per holding
- Portfolio returns by position_style (constant_mix / drift / periodic rebalance / dca)
- Outputs: holding_returns, portfolio_returns, nav series, end_of_period_weights
- For weekly/15min: also builds daily helper series from auxiliary daily data
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from data_loader import FREQ_CONFIG

# ---------------------------------------------------------------------------
# Rebalance interval: (data_frequency, trading_frequency) -> bars between snaps
# ---------------------------------------------------------------------------

REBALANCE_BARS: dict[tuple[str, str], int] = {
    ("daily", "weekly"): 5,
    ("daily", "monthly"): 21,
    ("daily", "quarterly"): 63,
    ("weekly", "weekly"): 1,
    ("weekly", "monthly"): 4,
    ("weekly", "quarterly"): 13,
}

# Risk-free rate (annual)
RF_ANNUAL = 0.015


# ---------------------------------------------------------------------------
# Simple returns
# ---------------------------------------------------------------------------

def compute_simple_returns(prices: pd.Series) -> pd.Series:
    """Compute simple returns from a price series. First element is NaN."""
    return prices.pct_change()


def _align_prices_to_common_index(
    prices_by_code: dict[str, pd.Series],
) -> dict[str, pd.Series]:
    """Restrict all price series to their common trading-date intersection."""
    if not prices_by_code:
        return {}

    common_index = None
    for prices in prices_by_code.values():
        idx = prices.dropna().index
        common_index = idx if common_index is None else common_index.intersection(idx)

    if common_index is None:
        return {}

    common_index = common_index.sort_values()
    return {
        code: prices.loc[common_index].astype(float)
        for code, prices in prices_by_code.items()
    }


# ---------------------------------------------------------------------------
# Portfolio return models
# ---------------------------------------------------------------------------

def _portfolio_constant_mix(
    holding_returns: dict[str, pd.Series],
    target_weights: dict[str, float],
    cash_weight: float,
    macro_ann_factor: int,
) -> pd.Series:
    """Constant-mix: every period snap back to target weights."""
    r_cash = RF_ANNUAL / macro_ann_factor
    codes = list(holding_returns.keys())
    df = pd.DataFrame(holding_returns).sort_index()
    if df.empty:
        return pd.Series(dtype=float)

    valid = df.notna().all(axis=1)
    r_port = df.mul(pd.Series(target_weights)).sum(axis=1, min_count=len(codes))
    r_port.loc[valid] = r_port.loc[valid] + cash_weight * r_cash
    r_port.loc[~valid] = np.nan
    return r_port


def _portfolio_drift(
    holding_returns: dict[str, pd.Series],
    initial_weights: dict[str, float],
    cash_weight: float,
    macro_ann_factor: int,
) -> tuple[pd.Series, dict[str, float]]:
    """Buy-and-hold / drift: weights evolve with price changes.

    Returns (portfolio_returns, end_of_period_weights_dict).
    """
    r_cash_per_period = RF_ANNUAL / macro_ann_factor
    codes = list(holding_returns.keys())
    df = pd.DataFrame(holding_returns).sort_index()
    if df.empty:
        return pd.Series(dtype=float), dict(initial_weights)
    n = len(df)

    # Track value of each slot
    values = {code: np.empty(n) for code in codes}
    cash_val = np.empty(n)
    port_val = np.empty(n)
    port_ret = np.empty(n)

    # Initialize at t=0 (first return is NaN, treat as 0)
    for code in codes:
        values[code][0] = initial_weights[code]
    cash_val[0] = cash_weight
    port_val[0] = sum(initial_weights.values()) + cash_weight  # should be 1.0

    for t in range(1, n):
        for code in codes:
            values[code][t] = values[code][t - 1] * (1 + float(df[code].iloc[t]))
        cash_val[t] = cash_val[t - 1] * (1 + r_cash_per_period)
        port_val[t] = sum(values[code][t] for code in codes) + cash_val[t]
        port_ret[t] = port_val[t] / port_val[t - 1] - 1

    port_ret[0] = np.nan
    r_series = pd.Series(port_ret, index=df.index)

    # End-of-period weights
    total = port_val[-1]
    eop_weights = {code: values[code][-1] / total for code in codes}

    return r_series, eop_weights


def _portfolio_periodic_rebalance(
    holding_returns: dict[str, pd.Series],
    target_weights: dict[str, float],
    cash_weight: float,
    macro_ann_factor: int,
    rebalance_interval: int,
) -> tuple[pd.Series, dict[str, float]]:
    """Periodic rebalance: drift between rebalance dates, snap back at interval.

    Returns (portfolio_returns, end_of_period_weights_dict).
    """
    r_cash_per_period = RF_ANNUAL / macro_ann_factor
    codes = list(holding_returns.keys())
    df = pd.DataFrame(holding_returns).sort_index()
    if df.empty:
        return pd.Series(dtype=float), dict(target_weights)
    n = len(df)

    values = {code: np.empty(n) for code in codes}
    cash_val = np.empty(n)
    port_val = np.empty(n)
    port_ret = np.empty(n)

    for code in codes:
        values[code][0] = target_weights[code]
    cash_val[0] = cash_weight
    port_val[0] = sum(target_weights.values()) + cash_weight

    for t in range(1, n):
        for code in codes:
            values[code][t] = values[code][t - 1] * (1 + float(df[code].iloc[t]))
        cash_val[t] = cash_val[t - 1] * (1 + r_cash_per_period)
        total = sum(values[code][t] for code in codes) + cash_val[t]

        # Snap back at rebalance points
        if t % rebalance_interval == 0:
            for code in codes:
                values[code][t] = target_weights[code] * total
            cash_val[t] = cash_weight * total

        port_val[t] = sum(values[code][t] for code in codes) + cash_val[t]
        port_ret[t] = port_val[t] / port_val[t - 1] - 1

    port_ret[0] = np.nan
    r_series = pd.Series(port_ret, index=df.index)

    total = port_val[-1]
    eop_weights = {code: values[code][-1] / total for code in codes}

    return r_series, eop_weights


# ---------------------------------------------------------------------------
# Build NAV series from prices (for daily helpers)
# ---------------------------------------------------------------------------

def build_nav_series(prices_by_code: dict[str, pd.Series]) -> dict[str, pd.Series]:
    """Build NAV series (cumulative value starting at 1.0) for each code."""
    navs = {}
    for code, prices in prices_by_code.items():
        returns = prices.pct_change().fillna(0)
        nav = (1 + returns).cumprod()
        navs[code] = nav
    return navs


def build_portfolio_nav(
    portfolio_returns: pd.Series,
) -> pd.Series:
    """Build portfolio NAV from a portfolio return series."""
    if portfolio_returns.empty:
        return pd.Series(dtype=float)
    return (1 + portfolio_returns.fillna(0)).cumprod()


def _run_portfolio_model(
    holding_returns: dict[str, pd.Series],
    holdings_weights: dict[str, float],
    cash_pct: float,
    position_style: str,
    trading_frequency: str,
    data_frequency: str,
    macro_ann_factor: int,
) -> tuple[pd.Series, dict[str, float], list[str]]:
    """Run the portfolio return model for one frequency view."""
    warnings: list[str] = []

    if position_style == "constant_mix":
        portfolio_returns = _portfolio_constant_mix(
            holding_returns, holdings_weights, cash_pct, macro_ann_factor,
        )
        eop_weights = dict(holdings_weights)
    elif position_style == "timing_rotation":
        portfolio_returns, eop_weights = _portfolio_drift(
            holding_returns, holdings_weights, cash_pct, macro_ann_factor,
        )
    elif position_style in ("full_rotation", "core_satellite"):
        key = (data_frequency, trading_frequency)
        interval = REBALANCE_BARS.get(key)
        if interval is None:
            portfolio_returns, eop_weights = _portfolio_drift(
                holding_returns, holdings_weights, cash_pct, macro_ann_factor,
            )
            warnings.append(
                f"No rebalance interval for ({data_frequency}, {trading_frequency}); "
                f"degraded to drift mode"
            )
        else:
            portfolio_returns, eop_weights = _portfolio_periodic_rebalance(
                holding_returns, holdings_weights, cash_pct, macro_ann_factor, interval,
            )
    elif position_style == "dca":
        key = (data_frequency, trading_frequency)
        interval = REBALANCE_BARS.get(key)
        if interval is None:
            portfolio_returns, eop_weights = _portfolio_drift(
                holding_returns, holdings_weights, cash_pct, macro_ann_factor,
            )
        else:
            portfolio_returns, eop_weights = _portfolio_periodic_rebalance(
                holding_returns, holdings_weights, cash_pct, macro_ann_factor, interval,
            )
        warnings.append(
            "DCA mode approximated as periodic rebalance "
            "(missing per-period injection amount input)"
        )
    else:
        raise ValueError(f"Unknown position_style: {position_style}")

    return portfolio_returns, eop_weights, warnings


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def compute_returns(
    prices_by_code: dict[str, pd.Series],
    holdings_weights: dict[str, float],
    cash_pct: float,
    position_style: str,
    trading_frequency: str,
    data_frequency: str,
    daily_prices_by_code: Optional[dict[str, pd.Series]] = None,
) -> dict:
    """Compute all return products.

    Args:
        prices_by_code: {code: Series[close]} at main frequency
        holdings_weights: {code: weight_fraction} (e.g. 0.30 for 30%)
        cash_pct: cash fraction (e.g. 0.15)
        position_style: determines return model
        trading_frequency: for periodic rebalance interval
        data_frequency: daily|weekly|15min
        daily_prices_by_code: auxiliary daily prices (for weekly/15min helpers)

    Returns dict with all products per plan Step 6 contract.
    """
    macro_ann_factor = FREQ_CONFIG[data_frequency]["macro_ann_factor"]
    warnings: list[str] = []

    prices_by_code = _align_prices_to_common_index(prices_by_code)
    if daily_prices_by_code is not None:
        daily_prices_by_code = _align_prices_to_common_index(daily_prices_by_code)

    # Individual holding returns at main frequency
    holding_returns = {
        code: compute_simple_returns(prices)
        for code, prices in prices_by_code.items()
    }

    portfolio_returns, eop_weights, model_warnings = _run_portfolio_model(
        holding_returns,
        holdings_weights,
        cash_pct,
        position_style,
        trading_frequency,
        data_frequency,
        macro_ann_factor,
    )
    warnings.extend(model_warnings)

    # Build daily helpers (for recovery_days, liquidity, etc.)
    # For daily: use main data; for weekly/15min: use auxiliary daily
    if data_frequency == "daily":
        src = prices_by_code
    elif daily_prices_by_code is not None:
        src = daily_prices_by_code
    else:
        src = None

    holding_nav_daily = None
    portfolio_nav_daily = None
    if src is not None:
        holding_nav_daily = build_nav_series(src)
        daily_helper_returns = {
            code: compute_simple_returns(prices)
            for code, prices in src.items()
        }
        helper_portfolio_returns, _, helper_warnings = _run_portfolio_model(
            daily_helper_returns,
            holdings_weights,
            cash_pct,
            position_style,
            trading_frequency,
            "daily",
            252,
        )
        warnings.extend(helper_warnings)
        portfolio_nav_daily = build_portfolio_nav(helper_portfolio_returns)

    # Intraday products (15min only)
    # For 15min scenarios, macro-level outputs (holding_returns, portfolio_returns)
    # are promoted to daily frequency when daily helper data is available.
    # The original 15min series are preserved as intraday_* outputs.
    intraday_holding_returns = None
    intraday_portfolio_returns = None
    intraday_ann_factor = None
    macro_holding_returns = holding_returns
    macro_portfolio_returns = portfolio_returns
    macro_eop_weights = eop_weights
    macro_af = macro_ann_factor

    if data_frequency == "15min":
        intraday_holding_returns = holding_returns
        intraday_portfolio_returns = portfolio_returns
        intraday_ann_factor = FREQ_CONFIG["15min"]["intraday_ann_factor"]
        if daily_prices_by_code is not None:
            daily_returns = {
                code: compute_simple_returns(p)
                for code, p in daily_prices_by_code.items()
            }
            macro_af = 252
            macro_portfolio_returns, macro_eop_weights, helper_warnings = _run_portfolio_model(
                daily_returns,
                holdings_weights,
                cash_pct,
                position_style,
                trading_frequency,
                "daily",
                macro_af,
            )
            warnings.extend(helper_warnings)
            macro_holding_returns = daily_returns

    return {
        "holding_returns": macro_holding_returns,
        "portfolio_returns": macro_portfolio_returns,
        "end_of_period_weights": macro_eop_weights,
        "macro_ann_factor": macro_af,
        "holding_nav_daily": holding_nav_daily,
        "portfolio_nav_daily": portfolio_nav_daily,
        "holding_returns_intraday": intraday_holding_returns,
        "portfolio_returns_intraday": intraday_portfolio_returns,
        "intraday_ann_factor": intraday_ann_factor,
        "warnings": warnings,
    }

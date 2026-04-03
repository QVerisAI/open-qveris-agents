"""
Risk metrics: 12 indicators per holding + portfolio.

All metrics use simple returns at the macro frequency.
Recovery days uses daily NAV series for all data frequencies.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

RF_ANNUAL = 0.015


def annualized_return_arithmetic(returns: pd.Series, ann_factor: int) -> float:
    r = returns.dropna()
    return float(r.mean() * ann_factor)


def cagr(returns: pd.Series, ann_factor: int) -> float:
    r = returns.dropna()
    total = (1 + r).prod() - 1
    n = len(r)
    if n == 0:
        return 0.0
    return float((1 + total) ** (ann_factor / n) - 1)


def annualized_volatility(returns: pd.Series, ann_factor: int) -> float:
    r = returns.dropna()
    return float(r.std(ddof=1) * np.sqrt(ann_factor))


def downside_volatility(returns: pd.Series, ann_factor: int) -> Optional[float]:
    r = returns.dropna()
    neg = r[r < 0]
    if len(neg) < 2:
        return None
    return float(neg.std(ddof=1) * np.sqrt(ann_factor))


def max_drawdown(returns: pd.Series) -> float:
    r = returns.dropna()
    if len(r) == 0:
        return 0.0
    cum = pd.concat([pd.Series([1.0]), (1 + r).cumprod()], ignore_index=True)
    running_max = cum.cummax()
    dd = (cum - running_max) / running_max
    return float(abs(dd.min()))


def max_dd_recovery_days(nav: pd.Series) -> Optional[int]:
    """Recovery days from max drawdown trough to new high, using daily NAV.

    Returns None if not yet recovered by end of series.
    """
    if nav is None or len(nav) == 0:
        return None

    nav = nav.reset_index(drop=True)
    running_max = nav.cummax()
    dd = (nav - running_max) / running_max
    if dd.min() == 0:
        return 0
    trough_pos = int(dd.idxmin())

    peak_val = running_max.iloc[trough_pos]

    # Search after trough for first point >= peak
    for i in range(trough_pos + 1, len(nav)):
        if nav.iloc[i] >= peak_val:
            return i - trough_pos

    return None


def sharpe_ratio(
    ann_return: float, ann_vol: float, rf: float = RF_ANNUAL,
) -> Optional[float]:
    if ann_vol == 0 or ann_vol is None:
        return None
    return (ann_return - rf) / ann_vol


def sortino_ratio(
    ann_return: float, down_vol: Optional[float], rf: float = RF_ANNUAL,
) -> Optional[float]:
    if down_vol is None or down_vol == 0:
        return None
    return (ann_return - rf) / down_vol


def calmar_ratio(cagr_val: float, mdd: float) -> Optional[float]:
    if mdd == 0:
        return None
    return cagr_val / mdd


def var_95(returns: pd.Series) -> float:
    r = returns.dropna()
    return float(np.percentile(r, 5))


def cvar_95(returns: pd.Series) -> float:
    r = returns.dropna()
    v = np.percentile(r, 5)
    return float(r[r <= v].mean())


def worst_period(returns: pd.Series) -> float:
    return float(returns.dropna().min())


def compute_all_metrics(
    returns: pd.Series,
    ann_factor: int,
    nav_daily: Optional[pd.Series] = None,
) -> dict:
    """Compute all 12 metrics for a single series (holding or portfolio)."""
    ann_ret = annualized_return_arithmetic(returns, ann_factor)
    cagr_val = cagr(returns, ann_factor)
    ann_vol = annualized_volatility(returns, ann_factor)
    down_vol = downside_volatility(returns, ann_factor)
    mdd = max_drawdown(returns)
    recovery = max_dd_recovery_days(nav_daily) if nav_daily is not None else None

    return {
        "ann_return_arithmetic": round(ann_ret, 4),
        "cagr": round(cagr_val, 4),
        "ann_volatility": round(ann_vol, 4),
        "downside_volatility": round(down_vol, 4) if down_vol is not None else None,
        "max_drawdown": round(mdd, 4),
        "max_dd_recovery_days": recovery,
        "sharpe_ratio": _round_opt(sharpe_ratio(ann_ret, ann_vol), 4),
        "sortino_ratio": _round_opt(sortino_ratio(ann_ret, down_vol), 4),
        "calmar_ratio": _round_opt(calmar_ratio(cagr_val, mdd), 4),
        "var_95": round(var_95(returns), 4),
        "cvar_95": round(cvar_95(returns), 4),
        "worst_period": round(worst_period(returns), 4),
    }


def _round_opt(val: Optional[float], decimals: int) -> Optional[float]:
    return round(val, decimals) if val is not None else None

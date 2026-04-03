"""
Liquidity analysis: liquidation days per holding based on actual position value.

Requires portfolio_market_value (total CNY) to compute meaningful results.
Uses 10% participation rate assumption (can only use 10% of daily volume without moving price).
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

PARTICIPATION_RATE = 0.10  # assume can trade 10% of daily volume without price impact
TURNOVER_WINDOW = 60  # use recent 60 trading days for turnover estimation


def compute_liquidity(
    daily_volumes_by_code: dict[str, pd.Series],
    daily_closes_by_code: dict[str, pd.Series],
    weights: dict[str, float],
    portfolio_market_value: float | None,
) -> dict:
    """Compute liquidation days per holding.

    liquidation_days = position_value / (avg_daily_turnover * participation_rate)

    If portfolio_market_value is None, only outputs avg_daily_turnover (no days).
    """
    # Avg daily turnover (CNY) per code
    turnovers: dict[str, float] = {}
    for code in weights:
        vol = daily_volumes_by_code.get(code)
        close = daily_closes_by_code.get(code)
        if vol is not None and close is not None:
            recent_turnover = vol.tail(TURNOVER_WINDOW) * close.tail(TURNOVER_WINDOW)
            turnovers[code] = float(recent_turnover.mean())
        else:
            turnovers[code] = 0.0

    holdings_liq = []
    max_days = None

    for code in weights:
        avg_turnover = turnovers[code]
        entry: dict = {
            "code": code,
            "avg_daily_turnover": round(avg_turnover, 0),
            "liquidation_days": None,
        }

        if portfolio_market_value is not None and avg_turnover > 0:
            position_value = weights[code] * portfolio_market_value
            tradeable_per_day = avg_turnover * PARTICIPATION_RATE
            days = position_value / tradeable_per_day
            entry["liquidation_days"] = round(days, 1)
            if max_days is None or days > max_days:
                max_days = days

        holdings_liq.append(entry)

    return {
        "portfolio_market_value": portfolio_market_value,
        "participation_rate": PARTICIPATION_RATE,
        "holdings": sorted(holdings_liq, key=lambda x: -(x["liquidation_days"] or 0)),
        "portfolio_max_liquidation_days": round(max_days, 1) if max_days is not None else None,
    }


def compute_intraday_micro(
    intraday_prices_by_code: Optional[dict[str, pd.Series]],
    intraday_volumes_by_code: Optional[dict[str, pd.Series]],
    intraday_datetimes: Optional[pd.Series],
    intraday_ann_factor: int = 4032,
) -> Optional[dict]:
    """Compute intraday volume/volatility by hour. Only for 15min data."""
    if intraday_prices_by_code is None or intraday_datetimes is None:
        return None

    vol_by_hour: dict[str, float] = {}
    for code, volumes in (intraday_volumes_by_code or {}).items():
        vol_series = volumes.copy()
        vol_series.index = pd.to_datetime(vol_series.index)
        df = pd.DataFrame({"hour": vol_series.index.hour, "vol": vol_series.values})
        hourly = df.groupby("hour")["vol"].mean()
        for h, v in hourly.items():
            key = f"{int(h):02d}:00"
            vol_by_hour[key] = vol_by_hour.get(key, 0) + float(v)

    vol_by_hour_volatility: dict[str, float] = {}
    for code, prices in intraday_prices_by_code.items():
        price_series = prices.copy()
        price_series.index = pd.to_datetime(price_series.index)
        returns = price_series.pct_change().dropna()
        df = pd.DataFrame({"hour": returns.index.hour, "ret": returns.values})
        hourly_vol = df.groupby("hour")["ret"].std()
        for h, v in hourly_vol.items():
            key = f"{int(h):02d}:00"
            vol_by_hour_volatility[key] = vol_by_hour_volatility.get(key, 0) + float(v)

    n_codes = len(intraday_prices_by_code)
    if n_codes > 0:
        vol_by_hour_volatility = {k: round(v / n_codes, 6) for k, v in vol_by_hour_volatility.items()}

    return {
        "intraday_ann_factor": intraday_ann_factor,
        "intraday_volume_by_hour": {k: round(v, 0) for k, v in sorted(vol_by_hour.items())},
        "intraday_volatility_by_hour": dict(sorted(vol_by_hour_volatility.items())),
    }

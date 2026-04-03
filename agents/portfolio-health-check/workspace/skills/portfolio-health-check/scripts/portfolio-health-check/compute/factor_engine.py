"""
Factor engine: compute 6 factors from daily prices + fundamentals.

Absolute anchor method — maps raw values to [-1, +1] based on A-share market characteristics.
All factors use daily data regardless of main data_frequency.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Anchor constants (A-share market)
# ---------------------------------------------------------------------------

SIZE_LOW = np.log(5e9)    # ln(50 yi) = ~22.3
SIZE_HIGH = np.log(5e11)  # ln(5000 yi) = ~26.9

MOMENTUM_LOW = -0.30
MOMENTUM_HIGH = 0.50

VOL_LOW = 0.10
VOL_HIGH = 0.50

LIQ_LOW = np.log(1e6)    # ln(1M volume)
LIQ_HIGH = np.log(5e7)   # ln(50M volume)

PE_LOW, PE_HIGH = 5, 80
PB_LOW, PB_HIGH = 0.5, 15
DIV_LOW, DIV_HIGH = 0, 6
ROE_LOW, ROE_HIGH = 0, 30
DEBT_LOW, DEBT_HIGH = 20, 90

# Momentum window parameters (trading days)
MOMENTUM_WINDOW_LONG = 250
MOMENTUM_WINDOW_MED = 126
MOMENTUM_SKIP = 20  # skip most recent N days to avoid short-term reversal
MOMENTUM_MIN_BARS = MOMENTUM_SKIP * 2 + 1  # minimum data points needed


def _norm(x: float, low: float, high: float) -> float:
    """Normalize to [-1, +1] and clip."""
    if high == low:
        return 0.0
    return max(-1.0, min(1.0, 2 * (x - low) / (high - low) - 1))


def _is_missing(value: float | None) -> bool:
    return value is None or pd.isna(value)


# ---------------------------------------------------------------------------
# Individual factor computations
# ---------------------------------------------------------------------------

def compute_size(market_cap: float) -> float:
    if _is_missing(market_cap) or float(market_cap) <= 0:
        return 0.0
    return _norm(np.log(market_cap), SIZE_LOW, SIZE_HIGH)


def compute_value(pe: float, pb: float, div_yield: float) -> float:
    scores: list[float] = []
    if not _is_missing(pe) and float(pe) > 0:
        scores.append(-_norm(float(pe), PE_LOW, PE_HIGH))
    if not _is_missing(pb) and float(pb) > 0:
        scores.append(-_norm(float(pb), PB_LOW, PB_HIGH))
    if not _is_missing(div_yield):
        scores.append(_norm(float(div_yield), DIV_LOW, DIV_HIGH))
    if not scores:
        return 0.0
    return float(sum(scores) / len(scores))


def compute_momentum(daily_prices: pd.Series) -> Optional[float]:
    """Momentum with auto-adaptive window.

    Standard: 250-day return excluding recent 20 days.
    Falls back to shorter windows if data insufficient.
    """
    prices = daily_prices.dropna().values
    n = len(prices)
    skip = MOMENTUM_SKIP + 1  # index offset: -21 means skip recent 20 days
    if n < MOMENTUM_MIN_BARS:
        return None
    if n >= MOMENTUM_WINDOW_LONG + MOMENTUM_SKIP:
        ret = prices[-skip] / prices[-(MOMENTUM_WINDOW_LONG + MOMENTUM_SKIP)] - 1
    elif n >= MOMENTUM_WINDOW_MED + MOMENTUM_SKIP:
        ret = prices[-skip] / prices[-(MOMENTUM_WINDOW_MED + MOMENTUM_SKIP)] - 1
    else:
        ret = prices[-skip] / prices[0] - 1
    return _norm(ret, MOMENTUM_LOW, MOMENTUM_HIGH)


def compute_quality(roe: float, debt_to_asset: float, earnings_stability: float) -> float:
    scores: list[float] = []
    if not _is_missing(roe):
        scores.append(_norm(float(roe), ROE_LOW, ROE_HIGH))
    if not _is_missing(debt_to_asset):
        scores.append(-_norm(float(debt_to_asset), DEBT_LOW, DEBT_HIGH))
    if not _is_missing(earnings_stability):
        scores.append(_norm(float(earnings_stability), 0, 1))
    if not scores:
        return 0.0
    return float(sum(scores) / len(scores))


def compute_volatility_factor(daily_prices: pd.Series) -> float:
    returns = daily_prices.pct_change().dropna()
    if len(returns) < 2:
        return 0.0
    ann_vol = float(returns.std() * np.sqrt(252))
    return _norm(ann_vol, VOL_LOW, VOL_HIGH)


def compute_liquidity_factor(daily_volumes: pd.Series) -> float:
    avg_vol = daily_volumes.tail(60).mean()
    if avg_vol <= 0 or np.isnan(avg_vol):
        return 0.0
    return _norm(np.log(avg_vol), LIQ_LOW, LIQ_HIGH)


# ---------------------------------------------------------------------------
# Main entry: compute all 6 factors for all holdings
# ---------------------------------------------------------------------------

def compute_all_factors(
    daily_prices_by_code: dict[str, pd.Series],
    daily_volumes_by_code: dict[str, pd.Series],
    fundamentals: pd.DataFrame,
) -> tuple[dict[str, dict[str, float]], Optional[int]]:
    """Compute 6 factors for each holding.

    Returns:
        (factor_scores, momentum_window) where factor_scores = {code: {factor: score}}
        and momentum_window = actual window length used for momentum.
    """
    results: dict[str, dict[str, float]] = {}
    momentum_window = None

    for _, row in fundamentals.iterrows():
        ticker = row["ticker"]
        scores: dict[str, float] = {}

        # Size, Value, Quality from fundamentals
        scores["size"] = round(compute_size(row.get("market_cap")), 2)
        scores["value"] = round(
            compute_value(
                row.get("pe_ttm"),
                row.get("pb"),
                row.get("dividend_yield"),
            ),
            2,
        )
        scores["quality"] = round(
            compute_quality(
                row.get("roe"),
                row.get("debt_to_asset"),
                row.get("earnings_stability"),
            ),
            2,
        )

        # Momentum, Volatility, Liquidity from daily prices/volumes
        if ticker in daily_prices_by_code:
            prices = daily_prices_by_code[ticker]
            mom = compute_momentum(prices)
            scores["momentum"] = round(mom, 2) if mom is not None else 0.0

            # Track momentum window for metadata
            if momentum_window is None and mom is not None:
                n = len(prices.dropna())
                if n >= MOMENTUM_WINDOW_LONG + MOMENTUM_SKIP:
                    momentum_window = MOMENTUM_WINDOW_LONG
                elif n >= MOMENTUM_WINDOW_MED + MOMENTUM_SKIP:
                    momentum_window = MOMENTUM_WINDOW_MED
                else:
                    momentum_window = n - MOMENTUM_SKIP - 1

            scores["volatility"] = round(compute_volatility_factor(prices), 2)
        else:
            scores["momentum"] = 0.0
            scores["volatility"] = 0.0

        if ticker in daily_volumes_by_code:
            scores["liquidity"] = round(compute_liquidity_factor(daily_volumes_by_code[ticker]), 2)
        else:
            scores["liquidity"] = 0.0

        results[ticker] = scores

    return results, momentum_window

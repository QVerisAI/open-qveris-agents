"""Benchmark-relative metrics: Beta, Alpha, TE, IR, relative max drawdown."""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

RF_ANNUAL = 0.015
CSI300_CODE = "000300.SH"
CSI500_CODE = "000905.SH"
BENCHMARK_NAMES = {
    CSI300_CODE: "沪深300",
    CSI500_CODE: "中证500",
}
LARGE_CAP_THRESHOLD = 50_000_000_000


def select_benchmark_details(
    holdings_weights: dict[str, float],
    fundamentals: pd.DataFrame,
) -> dict:
    """Select benchmark by weighted-average market cap and explain the choice.

    The average is computed across holdings with valid market cap data only.
    If no valid market cap is available, fall back to CSI300.
    """
    valid_total_weight = 0.0
    weighted_market_cap = 0.0

    for code, weight in holdings_weights.items():
        if weight <= 0:
            continue
        row = fundamentals[fundamentals["ticker"] == code]
        if len(row) == 0:
            continue

        market_cap = pd.to_numeric(
            pd.Series([row.iloc[0]["market_cap"]]),
            errors="coerce",
        ).iloc[0]
        if pd.isna(market_cap) or market_cap <= 0:
            continue

        valid_total_weight += weight
        weighted_market_cap += weight * float(market_cap)

    if valid_total_weight <= 0:
        return {
            "benchmark_code": CSI300_CODE,
            "benchmark_name": BENCHMARK_NAMES[CSI300_CODE],
            "weighted_avg_market_cap": None,
            "selection_rule": "缺少有效市值数据，默认使用沪深300",
        }

    avg_market_cap = weighted_market_cap / valid_total_weight
    benchmark_code = CSI300_CODE if avg_market_cap > LARGE_CAP_THRESHOLD else CSI500_CODE
    style_bucket = "大盘风格偏多" if benchmark_code == CSI300_CODE else "中小盘风格偏多"

    return {
        "benchmark_code": benchmark_code,
        "benchmark_name": BENCHMARK_NAMES[benchmark_code],
        "weighted_avg_market_cap": float(avg_market_cap),
        "selection_rule": (
            f"按持仓加权平均市值自动选基准：{style_bucket}，"
            f"{BENCHMARK_NAMES[benchmark_code]}更贴近组合风格"
        ),
    }


def select_benchmark(
    holdings_weights: dict[str, float],
    fundamentals: pd.DataFrame,
) -> str:
    """Auto-select benchmark by weighted average market cap.

    > 500亿 -> CSI300 (000300.SH), else -> CSI500 (000905.SH).
    """
    return select_benchmark_details(holdings_weights, fundamentals)["benchmark_code"]


def compute_benchmark_metrics(
    portfolio_returns: pd.Series,
    benchmark_returns: pd.Series,
    macro_ann_factor: int,
    rf: float = RF_ANNUAL,
) -> dict:
    """Compute Beta, Alpha, TE, IR, relative max drawdown."""
    # Align
    df = pd.DataFrame({
        "port": portfolio_returns,
        "bench": benchmark_returns,
    }).dropna()

    if len(df) < 5:
        return _empty_result()

    rp = df["port"]
    rb = df["bench"]

    ann_rp = float(rp.mean() * macro_ann_factor)
    ann_rb = float(rb.mean() * macro_ann_factor)

    var_b = rb.var(ddof=1)
    beta = None
    alpha = None
    if var_b != 0:
        beta = float(rp.cov(rb) / var_b)
        alpha = ann_rp - (rf + beta * (ann_rb - rf))

    excess = rp - rb
    te = float(excess.std(ddof=1) * np.sqrt(macro_ann_factor))
    ir = (ann_rp - ann_rb) / te if te > 0 else None

    # Relative max drawdown: (1+rp)/(1+rb) cumulative
    rel_cum = pd.concat(
        [
            pd.Series([1.0]),
            (((1 + rp) / (1 + rb)).cumprod()).reset_index(drop=True),
        ],
        ignore_index=True,
    )
    rel_peak = rel_cum.cummax()
    rel_dd = (rel_cum - rel_peak) / rel_peak
    rel_mdd = float(abs(rel_dd.min()))

    return {
        "beta": round(beta, 4) if beta is not None else None,
        "alpha_annual": round(alpha, 4) if alpha is not None else None,
        "tracking_error": round(te, 4),
        "information_ratio": round(ir, 4) if ir is not None else None,
        "relative_max_drawdown": round(rel_mdd, 4),
    }


def resample_to_weekly(daily_df: pd.DataFrame) -> pd.DataFrame:
    """Resample daily benchmark to weekly (last trading day of each week)."""
    df = daily_df.copy()
    df = df.set_index("datetime")
    weekly = df.resample("W-FRI").last().dropna().reset_index()
    return weekly


def _empty_result() -> dict:
    return {
        "beta": None,
        "alpha_annual": None,
        "tracking_error": None,
        "information_ratio": None,
        "relative_max_drawdown": None,
    }

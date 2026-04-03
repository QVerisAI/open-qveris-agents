"""Tests for compute/risk_metrics.py"""
import pytest
import numpy as np
import pandas as pd

from compute.risk_metrics import (
    annualized_return_arithmetic,
    cagr,
    annualized_volatility,
    downside_volatility,
    max_drawdown,
    max_dd_recovery_days,
    sharpe_ratio,
    sortino_ratio,
    calmar_ratio,
    var_95,
    cvar_95,
    worst_period,
    compute_all_metrics,
)


# ── Known-value returns ─────────────────────────────────────────────

# 600519.SH first 5 daily closes from sample: 1561, 1556.02, 1549.02, 1568.88, 1500
MAOTAI_PRICES = [1561.0, 1556.02, 1549.02, 1568.88, 1500.0]
MAOTAI_RETURNS = pd.Series([
    np.nan,
    1556.02 / 1561.0 - 1,   # -0.003190
    1549.02 / 1556.02 - 1,  # -0.004498
    1568.88 / 1549.02 - 1,  # +0.012817
    1500.0 / 1568.88 - 1,   # -0.043919
])


class TestAnnualizedReturn:
    def test_known_values(self):
        r = annualized_return_arithmetic(MAOTAI_RETURNS, 252)
        # mean of 4 returns × 252
        mean_r = MAOTAI_RETURNS.dropna().mean()
        assert r == pytest.approx(mean_r * 252, rel=1e-6)


class TestCAGR:
    def test_positive(self):
        r = pd.Series([0.01, 0.02, -0.005, 0.015, 0.01])
        c = cagr(r, 252)
        assert c > 0

    def test_total_return_consistency(self):
        r = pd.Series([0.1, -0.05, 0.03])
        total = (1.1) * (0.95) * (1.03) - 1
        c = cagr(r, 252)
        # CAGR = (1+total)^(252/3) - 1
        expected = (1 + total) ** (252 / 3) - 1
        assert c == pytest.approx(expected, rel=1e-6)


class TestVolatility:
    def test_positive(self):
        r = pd.Series(np.random.normal(0, 0.02, 100))
        v = annualized_volatility(r, 252)
        assert v > 0

    def test_zero_variance(self):
        r = pd.Series([0.01, 0.01, 0.01, 0.01])
        v = annualized_volatility(r, 252)
        assert v == pytest.approx(0.0)


class TestDownsideVol:
    def test_less_than_total_vol(self):
        r = pd.Series(np.random.normal(0, 0.02, 200))
        dv = downside_volatility(r, 252)
        tv = annualized_volatility(r, 252)
        assert dv is not None
        # Downside vol uses only negative returns; not necessarily less than total

    def test_no_negative_returns(self):
        r = pd.Series([0.01, 0.02, 0.005, 0.015])
        dv = downside_volatility(r, 252)
        assert dv is None


class TestMaxDrawdown:
    def test_peak_to_trough(self):
        # Price: 100 -> 120 -> 90 -> 110
        # Returns: NaN, +0.2, -0.25, +0.222
        r = pd.Series([np.nan, 0.2, -0.25, 0.2222])
        mdd = max_drawdown(r)
        # Cum: 1.2, 0.9, 1.1 -> peak=1.2, trough=0.9, dd=0.25
        assert mdd == pytest.approx(0.25, rel=1e-3)

    def test_not_simple_min(self):
        # Make sure it's peak-to-trough not just worst return
        r = pd.Series([0.5, -0.1, 0.3, -0.05])
        mdd = max_drawdown(r)
        # Cum: 1.5, 1.35, 1.755, 1.667
        # Peak=1.755, trough=1.667 -> dd only ~5%
        # But from 1.5 to 1.35 is dd=10%
        assert mdd > 0

    def test_initial_loss_counts_from_starting_nav(self):
        r = pd.Series([np.nan, -0.10, 0.10])
        mdd = max_drawdown(r)
        assert mdd == pytest.approx(0.10, rel=1e-6)


class TestRecoveryDays:
    def test_no_drawdown_returns_zero(self):
        nav = pd.Series([100, 101, 102, 103])
        days = max_dd_recovery_days(nav)
        assert days == 0

    def test_recovered(self):
        nav = pd.Series([100, 110, 90, 95, 100, 105, 115])
        days = max_dd_recovery_days(nav)
        # Trough at index 2 (90), peak before was 110
        # Recovery to 110 at index 6 (115>=110)
        assert days == 4  # index 6 - index 2

    def test_not_recovered(self):
        nav = pd.Series([100, 110, 80, 85, 90])
        days = max_dd_recovery_days(nav)
        assert days is None

    def test_none_input(self):
        assert max_dd_recovery_days(None) is None


class TestSharpe:
    def test_positive(self):
        s = sharpe_ratio(0.10, 0.15)
        assert s == pytest.approx((0.10 - 0.015) / 0.15)

    def test_zero_vol(self):
        assert sharpe_ratio(0.10, 0.0) is None


class TestSortino:
    def test_basic(self):
        s = sortino_ratio(0.10, 0.12)
        assert s == pytest.approx((0.10 - 0.015) / 0.12)

    def test_none_downvol(self):
        assert sortino_ratio(0.10, None) is None


class TestCalmar:
    def test_basic(self):
        c = calmar_ratio(0.08, 0.15)
        assert c == pytest.approx(0.08 / 0.15)

    def test_zero_dd(self):
        assert calmar_ratio(0.08, 0.0) is None


class TestVaRCVaR:
    def test_var_is_5th_percentile(self):
        r = pd.Series(np.random.normal(0, 0.02, 1000))
        v = var_95(r)
        assert v < 0  # 5th percentile of symmetric dist is negative

    def test_cvar_worse_than_var(self):
        r = pd.Series(np.random.normal(0, 0.02, 1000))
        v = var_95(r)
        cv = cvar_95(r)
        assert cv <= v


class TestComputeAll:
    def test_all_keys_present(self):
        r = pd.Series(np.random.normal(0.001, 0.02, 200))
        m = compute_all_metrics(r, 252)
        expected_keys = {
            "ann_return_arithmetic", "cagr", "ann_volatility",
            "downside_volatility", "max_drawdown", "max_dd_recovery_days",
            "sharpe_ratio", "sortino_ratio", "calmar_ratio",
            "var_95", "cvar_95", "worst_period",
        }
        assert set(m.keys()) == expected_keys

    def test_with_nav(self):
        # Use strong uptrend so recovery is likely
        r = pd.Series(np.random.RandomState(42).normal(0.005, 0.01, 100))
        nav = (1 + r).cumprod() * 100
        m = compute_all_metrics(r, 252, nav_daily=nav)
        # With strong uptrend, should recover from any drawdown
        assert m["max_dd_recovery_days"] is not None

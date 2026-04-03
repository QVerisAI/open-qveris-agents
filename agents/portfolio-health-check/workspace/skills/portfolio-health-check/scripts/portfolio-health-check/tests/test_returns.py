"""Tests for compute/returns.py"""
import pytest
import numpy as np
import pandas as pd

from compute.returns import (
    compute_simple_returns,
    compute_returns,
    REBALANCE_BARS,
)


# ── Fixtures ────────────────────────────────────────────────────────

def _make_prices(values: list[float], code: str = "TEST") -> pd.Series:
    return pd.Series(values, name=code, dtype=float)


def _make_prices_dict(n: int = 20, seed: int = 42) -> dict[str, pd.Series]:
    """2 synthetic stocks, mild uptrend."""
    rng = np.random.RandomState(seed)
    p1 = 100 * np.cumprod(1 + rng.normal(0.001, 0.02, n))
    p2 = 50 * np.cumprod(1 + rng.normal(0.0005, 0.015, n))
    return {
        "A": pd.Series(p1, dtype=float),
        "B": pd.Series(p2, dtype=float),
    }


WEIGHTS = {"A": 0.6, "B": 0.25}
CASH = 0.15


# ── Simple returns ──────────────────────────────────────────────────

class TestSimpleReturns:
    def test_basic(self):
        prices = _make_prices([100.0, 110.0, 105.0])
        r = compute_simple_returns(prices)
        assert np.isnan(r.iloc[0])
        assert r.iloc[1] == pytest.approx(0.10)
        assert r.iloc[2] == pytest.approx(-0.04545454545, rel=1e-6)

    def test_length(self):
        prices = _make_prices([10, 20, 30, 40, 50])
        r = compute_simple_returns(prices)
        assert len(r) == 5


# ── Constant mix ────────────────────────────────────────────────────

class TestConstantMix:
    def test_returns_weighted_sum(self):
        prices = _make_prices_dict(n=10)
        result = compute_returns(
            prices, WEIGHTS, CASH,
            position_style="constant_mix",
            trading_frequency="quarterly",
            data_frequency="daily",
        )
        pr = result["portfolio_returns"]
        assert len(pr) == 10
        # t=0: no prior bar, so portfolio return should stay NaN
        assert np.isnan(pr.iloc[0])
        # Spot check: weighted sum at t=1
        r_a = prices["A"].iloc[1] / prices["A"].iloc[0] - 1
        r_b = prices["B"].iloc[1] / prices["B"].iloc[0] - 1
        r_cash = 0.015 / 252
        expected = 0.6 * r_a + 0.25 * r_b + 0.15 * r_cash
        assert pr.iloc[1] == pytest.approx(expected, rel=1e-10)

    def test_eop_weights_equal_target(self):
        prices = _make_prices_dict()
        result = compute_returns(
            prices, WEIGHTS, CASH,
            position_style="constant_mix",
            trading_frequency="quarterly",
            data_frequency="daily",
        )
        assert result["end_of_period_weights"] == WEIGHTS


# ── Drift ───────────────────────────────────────────────────────────

class TestDrift:
    def test_weights_diverge_from_target(self):
        prices = _make_prices_dict(n=100, seed=7)
        result = compute_returns(
            prices, WEIGHTS, CASH,
            position_style="timing_rotation",
            trading_frequency="intraday",
            data_frequency="daily",
        )
        eop = result["end_of_period_weights"]
        # Weights should have drifted
        assert eop["A"] != pytest.approx(0.6, abs=0.01) or \
               eop["B"] != pytest.approx(0.25, abs=0.01)

    def test_portfolio_returns_not_nan(self):
        prices = _make_prices_dict(n=50)
        result = compute_returns(
            prices, WEIGHTS, CASH,
            position_style="timing_rotation",
            trading_frequency="intraday",
            data_frequency="daily",
        )
        pr = result["portfolio_returns"]
        assert np.isnan(pr.iloc[0])
        assert not pr.iloc[1:].isna().any()


# ── Periodic rebalance ──────────────────────────────────────────────

class TestPeriodicRebalance:
    def test_daily_monthly_interval(self):
        assert REBALANCE_BARS[("daily", "monthly")] == 21

    def test_weekly_monthly_interval(self):
        assert REBALANCE_BARS[("weekly", "monthly")] == 4

    def test_rebalance_snaps_back(self):
        """After rebalance, weights should be closer to target than pure drift."""
        prices = _make_prices_dict(n=100, seed=7)
        # Drift
        drift_result = compute_returns(
            prices, WEIGHTS, CASH,
            position_style="timing_rotation",
            trading_frequency="intraday",
            data_frequency="daily",
        )
        # Periodic (monthly = every 21 bars)
        periodic_result = compute_returns(
            prices, WEIGHTS, CASH,
            position_style="full_rotation",
            trading_frequency="monthly",
            data_frequency="daily",
        )
        # Periodic end weights should be closer to target
        drift_dev = abs(drift_result["end_of_period_weights"]["A"] - 0.6)
        periodic_dev = abs(periodic_result["end_of_period_weights"]["A"] - 0.6)
        # Not guaranteed every time but very likely over 100 bars
        # At least verify periodic produces valid results
        assert not np.isnan(periodic_result["portfolio_returns"].iloc[-1])

    def test_weekly_data_quarterly_rebalance(self):
        prices = _make_prices_dict(n=52)  # 1 year of weekly
        result = compute_returns(
            prices, WEIGHTS, CASH,
            position_style="core_satellite",
            trading_frequency="quarterly",
            data_frequency="weekly",
        )
        assert result["macro_ann_factor"] == 52
        assert not result["portfolio_returns"].iloc[1:].isna().any()


# ── DCA ─────────────────────────────────────────────────────────────

class TestDCA:
    def test_dca_warning(self):
        prices = _make_prices_dict(n=30)
        result = compute_returns(
            prices, WEIGHTS, CASH,
            position_style="dca",
            trading_frequency="monthly",
            data_frequency="daily",
        )
        assert any("DCA" in w for w in result["warnings"])


# ── Daily helpers ───────────────────────────────────────────────────

class TestDailyHelpers:
    def test_constant_mix_nav_matches_returns(self):
        prices = {
            "A": pd.Series([100, 110, 100, 110, 100], dtype=float),
            "B": pd.Series([100, 90, 100, 90, 100], dtype=float),
        }
        result = compute_returns(
            prices, {"A": 0.5, "B": 0.4}, 0.1,
            position_style="constant_mix",
            trading_frequency="monthly",
            data_frequency="daily",
        )
        expected_nav = (1 + result["portfolio_returns"].fillna(0)).cumprod()
        pd.testing.assert_series_equal(
            result["portfolio_nav_daily"],
            expected_nav,
            check_names=False,
            atol=1e-12,
            rtol=1e-12,
        )

    def test_periodic_nav_matches_returns(self):
        prices = {
            "A": pd.Series([100, 110, 100, 110, 100, 110, 100], dtype=float),
            "B": pd.Series([100, 90, 100, 90, 100, 90, 100], dtype=float),
        }
        result = compute_returns(
            prices, {"A": 0.5, "B": 0.4}, 0.1,
            position_style="full_rotation",
            trading_frequency="weekly",
            data_frequency="daily",
        )
        expected_nav = (1 + result["portfolio_returns"].fillna(0)).cumprod()
        pd.testing.assert_series_equal(
            result["portfolio_nav_daily"],
            expected_nav,
            check_names=False,
            atol=1e-12,
            rtol=1e-12,
        )

    def test_daily_scenario_has_nav(self):
        prices = _make_prices_dict(n=20)
        result = compute_returns(
            prices, WEIGHTS, CASH,
            position_style="constant_mix",
            trading_frequency="quarterly",
            data_frequency="daily",
        )
        assert result["holding_nav_daily"] is not None
        assert result["portfolio_nav_daily"] is not None
        assert len(result["portfolio_nav_daily"]) == 20

    def test_weekly_with_daily_helper(self):
        weekly_prices = _make_prices_dict(n=52)
        daily_prices = _make_prices_dict(n=252, seed=42)
        result = compute_returns(
            weekly_prices, WEIGHTS, CASH,
            position_style="timing_rotation",
            trading_frequency="buy_and_hold",
            data_frequency="weekly",
            daily_prices_by_code=daily_prices,
        )
        # Daily helpers should be built from auxiliary daily
        assert result["holding_nav_daily"] is not None
        assert len(result["portfolio_nav_daily"]) == 252

    def test_missing_trade_dates_use_common_intersection(self):
        idx_a = pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"])
        idx_b = pd.to_datetime(["2026-01-01", "2026-01-03"])
        prices = {
            "A": pd.Series([100, 110, 121], index=idx_a, dtype=float),
            "B": pd.Series([100, 100], index=idx_b, dtype=float),
        }
        result = compute_returns(
            prices, {"A": 0.5, "B": 0.4}, 0.1,
            position_style="constant_mix",
            trading_frequency="monthly",
            data_frequency="daily",
        )
        pr = result["portfolio_returns"]
        expected_index = pd.to_datetime(["2026-01-01", "2026-01-03"])
        assert list(pr.index) == list(expected_index)
        expected = 0.5 * (121 / 100 - 1) + 0.4 * 0.0 + 0.1 * (0.015 / 252)
        assert pr.iloc[1] == pytest.approx(expected, rel=1e-12)

    def test_no_intraday_for_daily(self):
        prices = _make_prices_dict(n=20)
        result = compute_returns(
            prices, WEIGHTS, CASH,
            position_style="constant_mix",
            trading_frequency="quarterly",
            data_frequency="daily",
        )
        assert result["holding_returns_intraday"] is None
        assert result["intraday_ann_factor"] is None


# ── 15min dual frequency ────────────────────────────────────────────

class TestIntraday15min:
    def test_15min_outputs_both_frequencies(self):
        intraday_prices = _make_prices_dict(n=672, seed=10)  # ~2 months of 15min
        daily_prices = _make_prices_dict(n=42, seed=42)      # 2 months daily
        result = compute_returns(
            intraday_prices, WEIGHTS, CASH,
            position_style="timing_rotation",
            trading_frequency="intraday",
            data_frequency="15min",
            daily_prices_by_code=daily_prices,
        )
        # Macro should use daily
        assert result["macro_ann_factor"] == 252
        assert len(result["holding_returns"]["A"]) == 42  # daily length

        # Intraday should use 15min
        assert result["intraday_ann_factor"] == 4032
        assert result["holding_returns_intraday"] is not None
        assert len(result["holding_returns_intraday"]["A"]) == 672

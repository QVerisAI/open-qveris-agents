"""Tests for factor_engine, factor_exposure, liquidity."""
import pytest
import numpy as np
import pandas as pd

from asset_paths import get_assets_dir
from compute.factor_engine import (
    _norm, compute_size, compute_value, compute_momentum,
    compute_quality, compute_volatility_factor, compute_liquidity_factor,
    compute_all_factors,
)
from compute.factor_exposure import compute_portfolio_factor_exposure, FACTOR_ORDER
from compute.liquidity import compute_liquidity

from data_loader import load_prices, load_fundamentals

ASSETS = get_assets_dir(__file__)


# ── Norm ────────────────────────────────────────────────────────────

class TestNorm:
    def test_midpoint(self):
        assert _norm(5, 0, 10) == pytest.approx(0.0)

    def test_low(self):
        assert _norm(0, 0, 10) == pytest.approx(-1.0)

    def test_high(self):
        assert _norm(10, 0, 10) == pytest.approx(1.0)

    def test_clips(self):
        assert _norm(20, 0, 10) == 1.0
        assert _norm(-5, 0, 10) == -1.0


# ── Size ────────────────────────────────────────────────────────────

class TestSize:
    def test_mega_cap(self):
        # 24500亿 = 2.45e12
        s = compute_size(2.45e12)
        assert s > 0.8  # should be near +1

    def test_small_cap(self):
        s = compute_size(5e9)  # 50亿
        assert s == pytest.approx(-1.0)


# ── Momentum ────────────────────────────────────────────────────────

class TestMomentum:
    def test_insufficient_data(self):
        prices = pd.Series([100, 101, 102])
        assert compute_momentum(prices) is None

    def test_positive_trend(self):
        # 250 days, strong uptrend
        prices = pd.Series(100 * np.cumprod(1 + np.full(250, 0.003)))
        m = compute_momentum(prices)
        assert m is not None
        assert m > 0

    def test_uses_shorter_window_when_needed(self):
        # 200 days: should use 146-day window
        prices = pd.Series(100 * np.cumprod(1 + np.full(200, 0.002)))
        m = compute_momentum(prices)
        assert m is not None


# ── Factor engine with sample data ──────────────────────────────────

class TestFactorEngine:
    def test_with_sample_data(self):
        daily = load_prices(ASSETS / "stock_data" / "stock_data" / "history_data_daily.csv")
        fund = load_fundamentals(ASSETS / "stock_data" / "fundamental_data.csv")

        prices_by_code = {
            code: grp.set_index("datetime")["close"]
            for code, grp in daily.groupby("code")
        }
        volumes_by_code = {
            code: grp.set_index("datetime")["volume"]
            for code, grp in daily.groupby("code")
        }

        factors, momentum_window = compute_all_factors(prices_by_code, volumes_by_code, fund)

        assert len(factors) == 4
        for code, scores in factors.items():
            assert set(scores.keys()) == set(FACTOR_ORDER)
            for f in FACTOR_ORDER:
                assert -1.0 <= scores[f] <= 1.0

        # 600519.SH should have high size score
        assert factors["600519.SH"]["size"] > 0.8

        # Momentum window should be 126 (only ~242 daily points)
        assert momentum_window == 126

    def test_missing_fundamental_columns_degrade_to_neutral_scores(self):
        prices_by_code = {
            "A": pd.Series(
                [100, 101, 103, 104, 105, 106] * 30,
                index=pd.date_range("2025-01-01", periods=180, freq="B"),
            ),
        }
        volumes_by_code = {
            "A": pd.Series(
                np.full(180, 1e7),
                index=pd.date_range("2025-01-01", periods=180, freq="B"),
            ),
        }
        fund = pd.DataFrame([{"ticker": "A", "market_cap": 8.5e10}])

        factors, momentum_window = compute_all_factors(prices_by_code, volumes_by_code, fund)

        assert factors["A"]["size"] != 0.0
        assert factors["A"]["value"] == 0.0
        assert factors["A"]["quality"] == 0.0
        assert factors["A"]["liquidity"] != 0.0
        assert momentum_window is not None

    def test_invalid_fundamentals_do_not_raise(self):
        prices_by_code = {
            "A": pd.Series(
                np.linspace(100, 160, 180),
                index=pd.date_range("2025-01-01", periods=180, freq="B"),
            ),
        }
        volumes_by_code = {
            "A": pd.Series(
                np.full(180, 5e6),
                index=pd.date_range("2025-01-01", periods=180, freq="B"),
            ),
        }
        fund = pd.DataFrame(
            [
                {
                    "ticker": "A",
                    "market_cap": np.nan,
                    "pe_ttm": -10,
                    "pb": np.nan,
                    "dividend_yield": np.nan,
                    "roe": np.nan,
                    "debt_to_asset": np.nan,
                    "earnings_stability": np.nan,
                }
            ]
        )

        factors, _ = compute_all_factors(prices_by_code, volumes_by_code, fund)

        assert factors["A"]["size"] == 0.0
        assert factors["A"]["value"] == 0.0
        assert factors["A"]["quality"] == 0.0


# ── Factor exposure ─────────────────────────────────────────────────

class TestFactorExposure:
    def test_weighted_average(self):
        holding_factors = {
            "A": {"size": 0.8, "value": 0.2, "momentum": 0.0, "quality": 0.5, "volatility": -0.3, "liquidity": 0.7},
            "B": {"size": -0.4, "value": 0.6, "momentum": 0.3, "quality": 0.1, "volatility": 0.2, "liquidity": 0.5},
        }
        weights = {"A": 0.6, "B": 0.25}
        result = compute_portfolio_factor_exposure(holding_factors, weights, 0.15)
        # Normalized: A=0.6/0.85=0.706, B=0.25/0.85=0.294
        expected_size = 0.706 * 0.8 + 0.294 * (-0.4)
        assert result["portfolio"]["size"] == pytest.approx(expected_size, abs=0.02)
        assert result["factor_order"] == FACTOR_ORDER

    def test_missing_tickers(self):
        holding_factors = {"A": {"size": 0.5, "value": 0, "momentum": 0, "quality": 0, "volatility": 0, "liquidity": 0}}
        weights = {"A": 0.6, "B": 0.25}
        result = compute_portfolio_factor_exposure(holding_factors, weights, 0.15)
        assert "B" in result["missing_tickers"]


# ── Liquidity scores ────────────────────────────────────────────────

class TestLiquidity:
    def test_with_market_value(self):
        """With portfolio_market_value, should compute liquidation_days."""
        n = 50
        vols = {"A": pd.Series(np.full(n, 1e7)), "B": pd.Series(np.full(n, 1e7))}
        closes = {"A": pd.Series(np.full(n, 100.0)), "B": pd.Series(np.full(n, 100.0))}
        weights = {"A": 0.5, "B": 0.5}
        # portfolio = 1M, each holding = 500K
        # daily turnover = 1e7 * 100 = 1e9, participation = 1e8
        # days = 500K / 1e8 = 0.005
        result = compute_liquidity(vols, closes, weights, 1_000_000)
        for h in result["holdings"]:
            assert h["liquidation_days"] is not None
            assert h["liquidation_days"] < 1  # trivial for small portfolio

    def test_without_market_value(self):
        """Without portfolio_market_value, liquidation_days should be None."""
        n = 50
        vols = {"A": pd.Series(np.full(n, 1e7))}
        closes = {"A": pd.Series(np.full(n, 100.0))}
        result = compute_liquidity(vols, closes, {"A": 1.0}, None)
        assert result["holdings"][0]["liquidation_days"] is None
        assert result["holdings"][0]["avg_daily_turnover"] > 0

    def test_large_portfolio_more_days(self):
        """Larger portfolio needs more days to liquidate."""
        n = 50
        vols = {"A": pd.Series(np.full(n, 1e6))}  # low volume
        closes = {"A": pd.Series(np.full(n, 10.0))}
        # turnover = 1e6 * 10 = 1e7, participation = 1e6
        # 10M portfolio, A=100% -> 10M / 1e6 = 10 days
        result = compute_liquidity(vols, closes, {"A": 1.0}, 10_000_000)
        assert result["holdings"][0]["liquidation_days"] == pytest.approx(10.0)
        assert result["portfolio_max_liquidation_days"] == pytest.approx(10.0)

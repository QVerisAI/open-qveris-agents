"""Tests for data_loader.py"""
import pytest
import pandas as pd

from asset_paths import get_assets_dir
from data_loader import (
    load_scenario,
    load_portfolio,
    load_prices,
    load_fundamentals,
    load_benchmark,
    truncate_by_lookback,
    ScenarioParams,
    Holding,
    FREQ_CONFIG,
    LOOKBACK_BARS,
    LOOKBACK_DAILY_BARS,
)

ASSETS = get_assets_dir(__file__)
SCENARIOS = ASSETS / "scenarios"
PORTFOLIOS = ASSETS / "sample-portfolios"
STOCK_DATA = ASSETS / "stock_data" / "stock_data"
FUND_DATA = ASSETS / "stock_data" / "fundamental_data.csv"
BENCH_DATA = ASSETS / "stock_data" / "benchmark_daily.csv"


# ── load_scenario ───────────────────────────────────────────────────

class TestLoadScenario:
    def test_moderate(self):
        s = load_scenario(SCENARIOS / "scenario_moderate.json")
        assert isinstance(s, ScenarioParams)
        assert s.data_frequency == "daily"
        assert s.trading_frequency == "quarterly"
        assert s.risk_tolerance == "moderate"
        assert s.investment_horizon == "3-5y"
        assert s.position_style == "core_satellite"
        assert s.lookback_period == "1y"

    def test_aggressive(self):
        s = load_scenario(SCENARIOS / "scenario_aggressive.json")
        assert s.data_frequency == "15min"  # 30min normalized to 15min
        assert s.trading_frequency == "intraday"
        assert s.risk_tolerance == "aggressive"
        assert s.position_style == "timing_rotation"

    def test_from_dict(self):
        s = load_scenario({
            "trading_frequency": "monthly",
            "data_frequency": "30min",
            "lookback_period": "3m",
            "position_style": "constant_mix",
            "risk_tolerance": "moderate",
            "investment_horizon": "1-3y",
            "portfolio_market_value": 1_000_000,
        })
        assert s.data_frequency == "15min"
        assert s.portfolio_market_value == 1_000_000


# ── load_portfolio ──────────────────────────────────────────────────

class TestLoadPortfolio:
    def test_a_shares_growth(self):
        holdings, cash_pct = load_portfolio(PORTFOLIOS / "sample_portfolio_a_shares_growth.csv")
        assert len(holdings) == 4  # 4 non-cash
        assert cash_pct == 15.0
        total = sum(h.weight_pct for h in holdings) + cash_pct
        assert total == pytest.approx(100.0)

    def test_holding_fields(self):
        holdings, _ = load_portfolio(PORTFOLIOS / "sample_portfolio_a_shares_growth.csv")
        maotai = [h for h in holdings if h.ticker == "600519.SH"][0]
        assert maotai.position_name == "贵州茅台"
        assert maotai.weight_pct == 30.0
        assert maotai.sector_theme == "consumer-staples"
        assert maotai.vehicle_type == "stock"
        assert not maotai.is_cash

    def test_cash_excluded_from_holdings(self):
        holdings, _ = load_portfolio(PORTFOLIOS / "sample_portfolio_a_shares_growth.csv")
        assert all(not h.is_cash for h in holdings)

    def test_from_api_payload(self):
        holdings, cash_pct = load_portfolio({
            "holdings": [
                {"code": "600519.SH", "weight_pct": 30.0},
                {"code": "300750.SZ", "weight_pct": 25.0},
            ],
            "cash_pct": 45.0,
        })
        assert [h.ticker for h in holdings] == ["600519.SH", "300750.SZ"]
        assert cash_pct == 45.0


# ── load_prices ─────────────────────────────────────────────────────

class TestLoadPrices:
    def test_daily_dtypes(self):
        df = load_prices(STOCK_DATA / "history_data_daily.csv")
        assert df["close"].dtype == float
        assert df["volume"].dtype == float
        assert "datetime" in df.columns
        assert pd.api.types.is_datetime64_any_dtype(df["datetime"])

    def test_daily_has_all_codes(self):
        df = load_prices(STOCK_DATA / "history_data_daily.csv")
        codes = set(df["code"].unique())
        assert "600519.SH" in codes
        assert "300750.SZ" in codes

    def test_weekly_loads(self):
        df = load_prices(STOCK_DATA / "history_data_weekly.csv")
        assert len(df) > 0
        assert df["close"].dtype == float

    def test_30min_has_datetime_col(self):
        df = load_prices(STOCK_DATA / "high_frequency_30min.csv")
        assert "datetime" in df.columns
        assert pd.api.types.is_datetime64_any_dtype(df["datetime"])

    def test_sorted_by_code_datetime(self):
        df = load_prices(STOCK_DATA / "history_data_daily.csv")
        for code in df["code"].unique():
            sub = df[df["code"] == code]
            assert sub["datetime"].is_monotonic_increasing

    def test_from_dataframe_with_date_alias(self):
        raw = pd.DataFrame([
            {"ticker": "600519.SH", "date": "2026-03-01", "close": "10.5", "volume": "1000"},
            {"ticker": "600519.SH", "date": "2026-03-02", "close": "10.8", "volume": "1200"},
        ])
        df = load_prices(raw)
        assert list(df.columns) == ["code", "datetime", "close", "volume"]
        assert df["code"].iloc[0] == "600519.SH"
        assert pd.api.types.is_datetime64_any_dtype(df["datetime"])


# ── load_fundamentals ───────────────────────────────────────────────

class TestLoadFundamentals:
    def test_loads_all_tickers(self):
        df = load_fundamentals(FUND_DATA)
        assert len(df) == 4
        assert set(df["ticker"]) == {"600519.SH", "300750.SZ", "510500.SH", "600036.SH"}

    def test_numeric_columns(self):
        df = load_fundamentals(FUND_DATA)
        assert pd.api.types.is_numeric_dtype(df["market_cap"])
        assert df["pe_ttm"].dtype == float
        assert df["roe"].dtype == float

    def test_etf_included(self):
        df = load_fundamentals(FUND_DATA)
        etf = df[df["ticker"] == "510500.SH"].iloc[0]
        assert etf["market_cap"] == 85000000000

    def test_from_dataframe_with_code_alias(self):
        df = load_fundamentals(pd.DataFrame([{"code": "600519.SH", "market_cap": "123"}]))
        assert df["ticker"].iloc[0] == "600519.SH"
        assert df["market_cap"].iloc[0] == 123


# ── load_benchmark ──────────────────────────────────────────────────

class TestLoadBenchmark:
    def test_filters_by_code(self):
        df = load_benchmark(BENCH_DATA, "000300.SH")
        assert len(df) > 0
        assert "datetime" in df.columns
        assert "close" in df.columns
        # Should not contain 000905
        # (load_benchmark returns only datetime+close, no code column)
        assert df["close"].dtype == float

    def test_csi500(self):
        df = load_benchmark(BENCH_DATA, "000905.SH")
        assert len(df) > 0

    def test_nonexistent_code_empty(self):
        df = load_benchmark(BENCH_DATA, "999999.SH")
        assert len(df) == 0

    def test_sorted_by_datetime(self):
        df = load_benchmark(BENCH_DATA, "000300.SH")
        assert df["datetime"].is_monotonic_increasing

    def test_from_dataframe_without_code_column(self):
        raw = pd.DataFrame([
            {"date": "2026-03-01", "close": "100.0"},
            {"date": "2026-03-02", "close": "101.0"},
        ])
        df = load_benchmark(raw, "000300.SH")
        assert len(df) == 2
        assert pd.api.types.is_datetime64_any_dtype(df["datetime"])


# ── truncate_by_lookback ────────────────────────────────────────────

class TestTruncate:
    def test_truncates_per_code(self):
        df = load_prices(STOCK_DATA / "history_data_daily.csv")
        truncated = truncate_by_lookback(df, 10)
        for code in truncated["code"].unique():
            assert len(truncated[truncated["code"] == code]) <= 10

    def test_keeps_last_n(self):
        df = load_prices(STOCK_DATA / "history_data_daily.csv")
        code = "600519.SH"
        original_last = df[df["code"] == code]["datetime"].max()
        truncated = truncate_by_lookback(df, 5)
        truncated_last = truncated[truncated["code"] == code]["datetime"].max()
        assert truncated_last == original_last

    def test_no_code_column(self):
        df = load_benchmark(BENCH_DATA, "000300.SH")
        truncated = truncate_by_lookback(df, 10)
        assert len(truncated) == 10


# ── Config tables ───────────────────────────────────────────────────

class TestConfig:
    def test_freq_config_keys(self):
        assert set(FREQ_CONFIG) == {"daily", "weekly", "15min"}
        assert FREQ_CONFIG["daily"]["macro_ann_factor"] == 252
        assert FREQ_CONFIG["weekly"]["macro_ann_factor"] == 52
        assert FREQ_CONFIG["15min"]["macro_ann_factor"] == 252
        assert FREQ_CONFIG["15min"]["intraday_ann_factor"] == 4032

    def test_lookback_daily_bars(self):
        assert LOOKBACK_DAILY_BARS["1y"] == 252
        assert LOOKBACK_DAILY_BARS["2m"] == 42

    def test_lookback_bars_weekly(self):
        assert LOOKBACK_BARS[("weekly", "1y")] == 52
        assert LOOKBACK_BARS[("weekly", "2y")] == 104

    def test_lookback_bars_15min(self):
        assert LOOKBACK_BARS[("15min", "2m")] == 672
        assert LOOKBACK_BARS[("15min", "3m")] == 1008

"""Integration tests: end-to-end with sample data."""
import pytest
import pandas as pd

from asset_paths import get_assets_dir
from diagnosis import run_diagnosis
from diagnosis_schema import validate_diagnosis_result, REQUIRED_DATA_SECTIONS

ASSETS = get_assets_dir(__file__)


def _run(scenario="scenario_moderate.json"):
    return run_diagnosis(
        ASSETS / "scenarios" / scenario,
        ASSETS / "sample-portfolios" / "sample_portfolio_a_shares_growth.csv",
        ASSETS / "stock_data" / "stock_data",
        ASSETS / "stock_data" / "fundamental_data.csv",
        ASSETS / "stock_data" / "benchmark_daily.csv",
    )


def _run_direct_daily():
    scenario = {
        "name": "direct_moderate",
        "trading_frequency": "quarterly",
        "data_frequency": "daily",
        "lookback_period": "1y",
        "position_style": "core_satellite",
        "risk_tolerance": "moderate",
        "investment_horizon": "3-5y",
        "portfolio_market_value": 5_000_000,
    }
    portfolio = {
        "holdings": [
            {"code": "600519.SH", "weight_pct": 30.0, "position_name": "贵州茅台", "sector_theme": "consumer-staples"},
            {"code": "300750.SZ", "weight_pct": 25.0, "position_name": "宁德时代", "sector_theme": "new-energy"},
            {"code": "510500.SH", "weight_pct": 20.0, "position_name": "中证500ETF", "sector_theme": "broad-equity"},
            {"code": "600036.SH", "weight_pct": 10.0, "position_name": "招商银行", "sector_theme": "banking"},
        ],
        "cash_pct": 15.0,
    }
    prices = pd.read_csv(ASSETS / "stock_data" / "stock_data" / "history_data_daily.csv")
    fundamentals = pd.read_csv(ASSETS / "stock_data" / "fundamental_data.csv")
    benchmark = pd.read_csv(ASSETS / "stock_data" / "benchmark_daily.csv")
    return run_diagnosis(scenario, portfolio, prices, fundamentals, benchmark)


def _run_direct_intraday():
    scenario = {
        "name": "direct_intraday",
        "trading_frequency": "intraday",
        "data_frequency": "15min",
        "lookback_period": "3m",
        "position_style": "timing_rotation",
        "risk_tolerance": "aggressive",
        "investment_horizon": "<1y",
        "portfolio_market_value": 2_000_000,
    }
    portfolio = {
        "holdings": [
            {"code": "600519.SH", "weight_pct": 40.0, "position_name": "贵州茅台", "sector_theme": "consumer-staples"},
            {"code": "300750.SZ", "weight_pct": 25.0, "position_name": "宁德时代", "sector_theme": "new-energy"},
            {"code": "510500.SH", "weight_pct": 20.0, "position_name": "中证500ETF", "sector_theme": "broad-equity"},
        ],
        "cash_pct": 15.0,
    }
    codes = {"600519.SH", "300750.SZ", "510500.SH"}
    prices = {
        "main": pd.read_csv(ASSETS / "stock_data" / "stock_data" / "high_frequency_30min.csv").query("code in @codes"),
        "daily": pd.read_csv(ASSETS / "stock_data" / "stock_data" / "history_data_daily.csv").query("code in @codes"),
    }
    fundamentals = pd.read_csv(ASSETS / "stock_data" / "fundamental_data.csv")
    benchmark = pd.read_csv(ASSETS / "stock_data" / "benchmark_daily.csv")
    return run_diagnosis(scenario, portfolio, prices, fundamentals, benchmark)


class TestModerateDaily:
    @pytest.fixture(scope="class")
    def result(self):
        return _run("scenario_moderate.json")

    def test_status_ok(self, result):
        assert result["status"] == "ok"

    def test_all_sections_present(self, result):
        d = result["data"]
        for key in sorted(REQUIRED_DATA_SECTIONS):
            assert key in d, f"Missing section: {key}"

    def test_strict_schema_validation(self, result):
        validate_diagnosis_result(result)

    def test_correlation_matrix_shape(self, result):
        cm = result["data"]["correlation_matrix"]
        n = len(cm["labels"])
        assert n == 4
        assert len(cm["matrix"]) == n
        assert all(len(row) == n for row in cm["matrix"])

    def test_risk_metrics_all_holdings(self, result):
        rm = result["data"]["risk_metrics"]
        assert len(rm["holdings"]) == 4
        assert "portfolio" in rm
        for h in rm["holdings"]:
            assert "ann_return_arithmetic" in h
            assert "sharpe_ratio" in h

    def test_risk_contribution_sums_to_one(self, result):
        rc = result["data"]["risk_contribution"]
        total = sum(h["pct_risk_contribution"] for h in rc["by_holding"])
        assert total == pytest.approx(1.0, abs=0.02)

    def test_concentration_values(self, result):
        c = result["data"]["concentration"]
        assert 0 < c["hhi"] < 1
        assert c["effective_n"] > 1
        assert c["max_holding_pct"] == 0.30

    def test_benchmark_selected(self, result):
        b = result["data"]["benchmark"]
        assert b["benchmark_code"] == "000300.SH"
        assert b["beta"] is not None

    def test_factor_exposure_complete(self, result):
        fe = result["data"]["factor_exposure"]
        assert fe["factor_order"] == ["size", "value", "momentum", "quality", "volatility", "liquidity"]
        assert len(fe["holdings"]) == 4
        for f in fe["factor_order"]:
            assert f in fe["portfolio"]
            assert -1 <= fe["portfolio"][f] <= 1

    def test_sector_exposure(self, result):
        se = result["data"]["sector_exposure"]
        assert len(se) == 4
        assert abs(sum(se.values()) - 0.85) < 0.01

    def test_liquidity(self, result):
        liq = result["data"]["liquidity"]
        assert len(liq["holdings"]) == 4

    def test_flags_sorted_by_severity(self, result):
        flags = result["data"]["risk_flags"]
        if len(flags) >= 2:
            severities = [f["severity"] for f in flags]
            high_last = max((i for i, s in enumerate(severities) if s == "high"), default=-1)
            medium_first = min((i for i, s in enumerate(severities) if s == "medium"), default=len(severities))
            assert high_last < medium_first or high_last == -1

    def test_metadata_complete(self, result):
        m = result["data"]["metadata"]
        assert m["data_frequency"] == "daily"
        assert m["annualization_factor"] == 252
        assert m["risk_tolerance"] == "moderate"
        assert m["return_method"] == "simple"
        assert m["factor_method"] == "absolute_anchor"
        assert m["factor_momentum_window"] is not None


class TestAggressiveIntraday:
    """Test with scenario_aggressive.json (data_frequency=30min, treated as 15min)."""

    @pytest.fixture(scope="class")
    def result(self):
        return _run("scenario_aggressive.json")

    def test_status_ok(self, result):
        assert result["status"] == "ok"

    def test_macro_ann_factor_is_252(self, result):
        # 15min macro uses daily helper -> ann_factor=252
        m = result["data"]["metadata"]
        assert m["annualization_factor"] == 252

    def test_benchmark_freq_note(self, result):
        m = result["data"]["metadata"]
        # Should have benchmark degradation warning
        assert m["benchmark_freq_note"] is not None or any(
            "15min" in w or "benchmark" in w
            for w in m["warnings"]
        )


class TestOutputContract:
    """Verify output matches the contract spec."""

    @pytest.fixture(scope="class")
    def result(self):
        return _run("scenario_moderate.json")

    def test_precision_ratios(self, result):
        """Ratios should be 4 decimal places."""
        pm = result["data"]["risk_metrics"]["portfolio"]
        for key in ["ann_return_arithmetic", "ann_volatility", "max_drawdown"]:
            val = pm[key]
            if val is not None:
                s = f"{val:.10f}"
                # Check 4 decimal places (not more significant)
                assert round(val, 4) == val

    def test_precision_correlation(self, result):
        """Correlation should be 2 decimal places."""
        matrix = result["data"]["correlation_matrix"]["matrix"]
        for row in matrix:
            for val in row:
                assert round(val, 2) == val

    def test_precision_factors(self, result):
        """Factor scores should be 2 decimal places."""
        for f, val in result["data"]["factor_exposure"]["portfolio"].items():
            assert round(val, 2) == val

    def test_factor_order_present(self, result):
        assert "factor_order" in result["data"]["factor_exposure"]
        assert len(result["data"]["factor_exposure"]["factor_order"]) == 6

    def test_risk_flags_have_required_fields(self, result):
        for f in result["data"]["risk_flags"]:
            assert "severity" in f
            assert "metric" in f
            assert "actual_value" in f
            assert "threshold" in f
            assert "explanation" in f


class TestDirectInputs:
    def test_run_diagnosis_accepts_dict_dataframe_inputs(self):
        result = _run_direct_daily()
        assert result["status"] == "ok"
        assert result["data"]["benchmark"]["benchmark_code"] == "000300.SH"
        assert result["data"]["metadata"]["weighted_avg_market_cap"] is not None

    def test_intraday_bundle_accepts_main_and_daily_frames(self):
        result = _run_direct_intraday()
        assert result["status"] == "ok"
        assert result["data"]["metadata"]["data_frequency"] == "15min"
        assert result["data"]["metadata"]["benchmark_freq_note"] is not None

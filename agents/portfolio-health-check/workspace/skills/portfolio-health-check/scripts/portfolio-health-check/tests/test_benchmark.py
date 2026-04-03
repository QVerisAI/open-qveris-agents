"""Tests for compute/benchmark.py"""
import pytest
import numpy as np
import pandas as pd

from asset_paths import get_assets_dir
from compute.benchmark import (
    select_benchmark,
    select_benchmark_details,
    compute_benchmark_metrics,
    resample_to_weekly,
)
from data_loader import load_fundamentals, load_benchmark

ASSETS = get_assets_dir(__file__)
FUND_DATA = ASSETS / "stock_data" / "fundamental_data.csv"
BENCH_DATA = ASSETS / "stock_data" / "benchmark_daily.csv"


class TestSelectBenchmark:
    def test_threshold_is_500_yi_not_5000_yi(self):
        fund = pd.DataFrame([{"ticker": "MID", "market_cap": 100_000_000_000}])
        code = select_benchmark({"MID": 1.0}, fund)
        assert code == "000300.SH"

    def test_sample_portfolio_selects_csi300(self):
        fund = load_fundamentals(FUND_DATA)
        # Sample portfolio: large-cap dominated
        weights = {"600519.SH": 0.353, "300750.SZ": 0.294, "510500.SH": 0.235, "600036.SH": 0.118}
        code = select_benchmark(weights, fund)
        assert code == "000300.SH"

    def test_small_cap_selects_csi500(self):
        fund = pd.DataFrame([{"ticker": "SMALL", "market_cap": 20_000_000_000}])
        weights = {"SMALL": 1.0}
        code = select_benchmark(weights, fund)
        assert code == "000905.SH"

    def test_selection_details_include_weighted_market_cap_and_reason(self):
        fund = pd.DataFrame([{"ticker": "MID", "market_cap": 100_000_000_000}])
        details = select_benchmark_details({"MID": 1.0}, fund)
        assert details["benchmark_code"] == "000300.SH"
        assert details["weighted_avg_market_cap"] == pytest.approx(100_000_000_000)
        assert "加权平均市值" in details["selection_rule"]

    def test_missing_market_cap_does_not_dilute_valid_weight_average(self):
        fund = pd.DataFrame([{"ticker": "LARGE", "market_cap": 100_000_000_000}])
        weights = {"LARGE": 0.2, "UNKNOWN": 0.8}
        details = select_benchmark_details(weights, fund)
        assert details["benchmark_code"] == "000300.SH"
        assert details["weighted_avg_market_cap"] == pytest.approx(100_000_000_000)

    def test_no_valid_market_cap_falls_back_to_csi300(self):
        fund = pd.DataFrame([{"ticker": "BAD", "market_cap": np.nan}])
        details = select_benchmark_details({"BAD": 1.0}, fund)
        assert details["benchmark_code"] == "000300.SH"
        assert details["weighted_avg_market_cap"] is None
        assert "默认使用沪深300" in details["selection_rule"]


class TestBenchmarkMetrics:
    def test_basic(self):
        rng = np.random.RandomState(42)
        n = 200
        rp = pd.Series(rng.normal(0.001, 0.015, n))
        rb = pd.Series(rng.normal(0.0008, 0.012, n))
        m = compute_benchmark_metrics(rp, rb, 252)
        assert m["beta"] is not None
        assert m["alpha_annual"] is not None
        assert m["tracking_error"] is not None
        assert m["tracking_error"] > 0

    def test_insufficient_data(self):
        rp = pd.Series([0.01, 0.02])
        rb = pd.Series([0.005, 0.01])
        m = compute_benchmark_metrics(rp, rb, 252)
        assert m["beta"] is None

    def test_relative_max_drawdown_counts_initial_drop(self):
        rp = pd.Series([-0.10, 0.10, 0.0, 0.0, 0.0])
        rb = pd.Series([0.01, 0.0, 0.0, 0.0, 0.0])
        m = compute_benchmark_metrics(rp, rb, 252)
        assert m["relative_max_drawdown"] == pytest.approx(1 - (0.9 / 1.01), rel=1e-4)


class TestResampleWeekly:
    def test_reduces_rows(self):
        bench = load_benchmark(BENCH_DATA, "000300.SH")
        weekly = resample_to_weekly(bench)
        assert len(weekly) < len(bench)
        assert len(weekly) > 0
        assert "datetime" in weekly.columns

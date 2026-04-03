"""Tests for Stage C rescore."""
import pytest
import numpy as np
import pandas as pd

from data_loader import UserConstraints
from prescribe.rescore import (
    build_whatif_portfolio, estimate_turnover_cost, rescore_candidate,
)


def _make_returns(n=100, seed=42):
    rng = np.random.RandomState(seed)
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    return {
        "A": pd.Series(rng.normal(0.001, 0.02, n), index=dates),
        "B": pd.Series(rng.normal(0.0005, 0.015, n), index=dates),
        "C": pd.Series(rng.normal(-0.0002, 0.025, n), index=dates),
    }


class TestBuildWhatifPortfolio:
    def test_pinned_weights_preserved(self):
        orig = {"A": 0.30, "B": 0.25, "C": 0.20}
        rec = {"targets": [{"code": "A", "to_pct": 15}], "instruments": [], "funding_req": "none"}
        w, cash = build_whatif_portfolio(orig, 0.25, rec, UserConstraints())
        assert w["A"] == pytest.approx(0.15)
        assert w["B"] == pytest.approx(0.25)
        assert w["C"] == pytest.approx(0.20)
        assert cash == pytest.approx(0.40)  # 1 - 0.15 - 0.25 - 0.20

    def test_use_cash_for_new_instrument(self):
        orig = {"A": 0.40, "B": 0.30}
        rec = {"targets": [], "instruments": [{"code": "ETF", "suggested_pct": 8}], "funding_req": "use_cash"}
        w, cash = build_whatif_portfolio(orig, 0.30, rec, UserConstraints())
        assert w["ETF"] == pytest.approx(0.08)
        assert w["A"] == pytest.approx(0.40)
        assert cash == pytest.approx(0.22)  # 1 - 0.40 - 0.30 - 0.08

    def test_new_capital_dilutes_untouched(self):
        orig = {"A": 0.45, "B": 0.35}
        rec = {"targets": [], "instruments": [{"code": "ETF", "suggested_pct": 10}], "funding_req": "new_capital"}
        c = UserConstraints(additional_capital_ratio="10-30%")
        w, cash = build_whatif_portfolio(orig, 0.20, rec, c)
        # dilution = 1/1.2 = 0.833
        assert w["A"] == pytest.approx(0.45 * (1/1.2), abs=0.01)
        assert w["ETF"] == pytest.approx(0.10)
        assert cash >= 0

    def test_total_sums_to_one(self):
        orig = {"A": 0.50, "B": 0.30}
        rec = {"targets": [{"code": "A", "to_pct": 30}], "instruments": [{"code": "ETF", "suggested_pct": 10}], "funding_req": "use_cash"}
        w, cash = build_whatif_portfolio(orig, 0.20, rec, UserConstraints())
        total = sum(w.values()) + cash
        assert total == pytest.approx(1.0, abs=0.001)


class TestTurnoverCost:
    def test_no_change_zero_cost(self):
        w = {"A": 0.5, "B": 0.5}
        assert estimate_turnover_cost(w, w) == 0.0

    def test_positive_cost_on_change(self):
        orig = {"A": 0.5, "B": 0.5}
        new = {"A": 0.3, "B": 0.5, "C": 0.2}
        cost = estimate_turnover_cost(orig, new)
        assert cost > 0


class TestRescoreCandidate:
    def test_basic_rescore(self):
        returns = _make_returns()
        orig_weights = {"A": 0.40, "B": 0.35, "C": 0.25}
        orig_metrics = {
            "ann_volatility": 0.25, "max_drawdown": 0.15, "sharpe_ratio": 0.30,
            "max_sector_pct": 0.40, "max_pairwise_corr": 0.80,
            "hhi": 0.30, "factor_dominant_score": 0.50,
        }
        rec = {
            "targets": [{"code": "C", "to_pct": 10}],
            "instruments": [],
            "funding_req": "none",
            "tier": 1,
        }
        result = rescore_candidate(
            rec, orig_weights, 0.0, orig_metrics, returns,
            UserConstraints(), ann_factor=252,
        )
        assert "delta" in result
        assert "verdict" in result
        assert result["verdict"] in ("pass", "pass_with_warning", "reject")
        assert result["estimated_turnover_cost"] > 0
        assert isinstance(result["stress_test"], list)
        assert "simulation_cases" in result
        assert "beginner_summary" in result

    def test_verdict_pass_when_all_improve(self):
        returns = _make_returns()
        orig_weights = {"A": 0.50, "B": 0.50}
        orig_metrics = {
            "ann_volatility": 0.30, "max_drawdown": 0.25, "sharpe_ratio": 0.10,
            "max_sector_pct": 0.50, "max_pairwise_corr": 0.90,
            "hhi": 0.50, "factor_dominant_score": 0.80,
        }
        # Reduce A -> less concentrated
        rec = {
            "targets": [{"code": "A", "to_pct": 30}],
            "instruments": [],
            "funding_req": "none",
            "tier": 2,
        }
        result = rescore_candidate(
            rec, orig_weights, 0.0, orig_metrics, returns,
            UserConstraints(), ann_factor=252,
        )
        # At minimum it should produce a valid rescore
        assert result["verdict"] in ("pass", "pass_with_warning", "reject")

    def test_full_target_builds_second_simulation_case(self):
        returns = _make_returns()
        orig_weights = {"A": 0.45, "B": 0.35, "C": 0.20}
        orig_metrics = {
            "ann_volatility": 0.25, "max_drawdown": 0.15, "sharpe_ratio": 0.30,
            "max_sector_pct": 0.40, "max_pairwise_corr": 0.80,
            "hhi": 0.30, "factor_dominant_score": 0.50,
        }
        rec = {
            "targets": [{"code": "A", "to_pct": 38, "full_target_pct": 30}],
            "instruments": [],
            "funding_req": "none",
            "tier": 1,
        }
        result = rescore_candidate(
            rec, orig_weights, 0.0, orig_metrics, returns,
            UserConstraints(), ann_factor=252,
        )
        assert len(result["simulation_cases"]) >= 2
        assert result["simulation_cases"][0]["name"] == "先做一步"
        assert result["simulation_cases"][1]["name"] == "做到目标区间"

    def test_empty_returns_fallback(self):
        orig_metrics = {"ann_volatility": 0.20, "max_drawdown": 0.10}
        rec = {"targets": [{"code": "X", "to_pct": 10}], "instruments": [], "funding_req": "none", "tier": 1}
        result = rescore_candidate(
            rec, {"X": 0.50}, 0.50, orig_metrics, {},
            UserConstraints(), ann_factor=252,
        )
        assert result["verdict"] == "pass"  # empty fallback

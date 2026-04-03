"""Tests for risk_flags + style_adjuster."""
import pytest
from data_loader import Holding
from compute.risk_flags import evaluate_flags
from compute.style_adjuster import adjust_flags


def _moderate_metrics():
    return {
        "ann_volatility": 0.25,  # above moderate 0.20
        "max_drawdown": 0.18,    # above moderate 0.15
        "sharpe_ratio": 0.45,    # below moderate 0.5
    }


def _moderate_concentration():
    return {
        "max_holding_pct": 0.30,  # above moderate 0.25
        "max_sector_pct": 0.35,   # above moderate 0.30
    }


class TestEvaluateFlags:
    def test_moderate_triggers(self):
        flags = evaluate_flags(
            _moderate_metrics(),
            [],  # no high corr
            {"size": 0.3, "value": 0.1, "momentum": 0, "quality": 0.2, "volatility": 0, "liquidity": 0.3},
            _moderate_concentration(),
            "moderate", "1-3y",
        )
        metrics = {f["metric"] for f in flags}
        assert "portfolio_vol_high" in metrics
        assert "max_dd_deep" in metrics
        assert "holding_cap" in metrics
        assert "sector_cap" in metrics
        assert "low_sharpe" in metrics

    def test_sorted_high_first(self):
        flags = evaluate_flags(
            _moderate_metrics(), [], {},
            _moderate_concentration(),
            "moderate", "1-3y",
        )
        severities = [f["severity"] for f in flags]
        high_idx = [i for i, s in enumerate(severities) if s == "high"]
        medium_idx = [i for i, s in enumerate(severities) if s == "medium"]
        if high_idx and medium_idx:
            assert max(high_idx) < min(medium_idx)

    def test_horizon_relaxes(self):
        # >5y should relax vol threshold: 0.20 * 1.5 = 0.30
        # Our vol is 0.25 < 0.30 → no flag
        flags = evaluate_flags(
            {"ann_volatility": 0.25, "max_drawdown": 0.10, "sharpe_ratio": 0.6},
            [], {}, {"max_holding_pct": 0.20, "max_sector_pct": 0.20},
            "moderate", ">5y",
        )
        metrics = {f["metric"] for f in flags}
        assert "portfolio_vol_high" not in metrics

    def test_very_aggressive_none_thresholds(self):
        flags = evaluate_flags(
            {"ann_volatility": 0.50, "max_drawdown": 0.30, "sharpe_ratio": 0.35},
            [], {}, {"max_holding_pct": 0.80, "max_sector_pct": 0.90},
            "very_aggressive", "1-3y",
        )
        metrics = {f["metric"] for f in flags}
        # vol, sector, holding should NOT trigger (None threshold)
        assert "portfolio_vol_high" not in metrics
        assert "sector_cap" not in metrics
        assert "holding_cap" not in metrics

    def test_high_correlation_flag(self):
        pairs = [{"pair": ["A", "B"], "correlation": 0.85}]
        flags = evaluate_flags(
            {"ann_volatility": 0.10, "max_drawdown": 0.05, "sharpe_ratio": 1.0},
            pairs, {}, {"max_holding_pct": 0.10, "max_sector_pct": 0.10},
            "moderate", "1-3y",
        )
        metrics = {f["metric"] for f in flags}
        assert "high_correlation" in metrics

    def test_factor_dominance(self):
        factors = {"size": 0.8, "value": 0.1, "momentum": 0, "quality": 0, "volatility": 0, "liquidity": 0}
        flags = evaluate_flags(
            {"ann_volatility": 0.10, "max_drawdown": 0.05, "sharpe_ratio": 1.0},
            [], factors, {"max_holding_pct": 0.10, "max_sector_pct": 0.10},
            "moderate", "1-3y",
        )
        factor_flags = [f for f in flags if f["metric"] == "factor_dominance"]
        assert len(factor_flags) == 1
        assert factor_flags[0]["actual_value"] == 0.8


class TestStyleAdjuster:
    def test_timing_suppresses_holding_cap(self):
        flags = [
            {"severity": "high", "metric": "holding_cap", "actual_value": 0.4, "threshold": 0.25, "explanation": ""},
            {"severity": "high", "metric": "portfolio_vol_high", "actual_value": 0.3, "threshold": 0.2, "explanation": ""},
        ]
        warnings = []
        result = adjust_flags(flags, "timing_rotation", [], warnings)
        metrics = {f["metric"] for f in result}
        assert "holding_cap" not in metrics
        assert "portfolio_vol_high" in metrics
        assert any("timing_rotation" in w for w in warnings)

    def test_full_rotation_escalates(self):
        flags = [
            {"severity": "medium", "metric": "holding_cap", "actual_value": 0.3, "threshold": 0.25, "explanation": ""},
        ]
        result = adjust_flags(flags, "full_rotation", [], [])
        assert result[0]["severity"] == "high"

    def test_core_satellite_warning(self):
        holdings = [
            Holding("A", "A", 40, risk_role="growth"),
            Holding("B", "B", 30, risk_role="growth"),
            Holding("C", "C", 15, risk_role="diversifier"),
        ]
        warnings = []
        adjust_flags([], "core_satellite", holdings, warnings)
        assert any("卫星部分" in w for w in warnings)

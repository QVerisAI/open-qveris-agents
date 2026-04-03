"""Integration tests for Phase 3 prescription pipeline."""
import pytest
import numpy as np
import pandas as pd

from data_loader import Holding, UserConstraints
from prescription import run_prescription, classify_entry


REQUIRED_TOP_KEYS = {"recommendations", "exclusive_groups", "asset_alignment", "constraints_applied", "summary", "client_output"}
REQUIRED_REC_KEYS = {
    "id", "tier", "priority", "composite_score", "source_flags", "category",
    "actionability", "funding_req", "access_req", "action", "rationale",
    "potential_side_effects", "assumptions", "targets", "instruments",
    "derivative_strategy", "rescore",
}


def _make_returns(codes, n=100, seed=42):
    rng = np.random.RandomState(seed)
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    return {c: pd.Series(rng.normal(0.001, 0.02, n), index=dates) for c in codes}


def _make_diagnosis(flags, holdings_data):
    """Build a bare diagnosis data dict (NOT wrapped in envelope)."""
    holdings = []
    for h in holdings_data:
        holdings.append({
            "code": h["code"], "name": h.get("name", h["code"]),
            "weight_pct": h["weight_pct"],
            "ann_volatility": h.get("vol", 0.25),
            "max_drawdown": h.get("dd", 0.15),
            "max_dd_recovery_days": h.get("rec", 30),
            "sharpe_ratio": h.get("sharpe", 0.3),
            "ann_return_arithmetic": h.get("ret", 0.08),
            "sector_theme": h.get("sector", ""),
        })
    weights_sum = sum(h["weight_pct"] for h in holdings)
    rc_entries = [{"code": h["code"], "weight_normalized": h["weight_pct"] / weights_sum, "pct_risk_contribution": h["weight_pct"] / weights_sum} for h in holdings]

    return {
        "risk_flags": flags,
        "risk_metrics": {
            "portfolio": {"ann_volatility": 0.28, "max_drawdown": 0.22, "sharpe_ratio": 0.15},
            "holdings": holdings,
        },
        "risk_contribution": {"by_holding": rc_entries},
        "concentration": {"effective_n": len(holdings), "max_holding_pct": max(h["weight_pct"] for h in holdings) / 100, "hhi": 0.30, "max_sector_pct": 0.40},
        "correlation_matrix": {"labels": [h["code"] for h in holdings], "matrix": [], "high_correlation_pairs": []},
        "factor_exposure": {"portfolio": {}, "holdings": {}},
        "sector_exposure": {"sectors": [], "portfolio": []},
        "liquidity": {"portfolio_max_liquidation_days": 2.0, "holdings": [{"code": h["code"], "liquidation_days": 1.0, "avg_daily_turnover": 5e6} for h in holdings]},
        "benchmark": {"benchmark_code": "000300.SH", "beta": 1.0},
        "intraday_micro": None,
        "metadata": {"annualization_factor": 252, "risk_tolerance": "moderate", "data_frequency": "daily"},
    }


class TestClassifyEntry:
    def test_no_flags(self):
        assert classify_entry([]) == "no_action"

    def test_one_medium(self):
        assert classify_entry([{"severity": "medium"}]) == "light_touch"

    def test_one_high(self):
        assert classify_entry([{"severity": "high"}]) == "actionable"

    def test_two_medium(self):
        assert classify_entry([{"severity": "medium"}, {"severity": "medium"}]) == "actionable"


class TestNoAction:
    def test_no_flags_returns_no_action(self):
        diag = _make_diagnosis([], [{"code": "A", "weight_pct": 90}])
        result = run_prescription(diag, UserConstraints())
        assert result["recommendations"]["status"] == "no_action"
        assert REQUIRED_TOP_KEYS <= set(result.keys())
        assert result["summary"]["total"] == 0


class TestLightTouch:
    def test_one_medium_flag(self):
        flags = [{"severity": "medium", "metric": "low_sharpe", "actual_value": 0.15, "threshold": 0.50}]
        diag = _make_diagnosis(flags, [
            {"code": "A", "weight_pct": 50, "sharpe": -0.1, "ret": -0.02},
            {"code": "B", "weight_pct": 40, "sharpe": 0.8, "ret": 0.15},
        ])
        result = run_prescription(diag, UserConstraints())
        assert result["recommendations"]["status"] == "light_touch"
        items = result["recommendations"]["tier_1"]["items"]
        assert len(items) == 1
        assert items[0]["category"] == "observe"
        assert items[0]["actionability"] == "directional"
        # Verify all required fields
        assert REQUIRED_REC_KEYS <= set(items[0].keys())


class TestActionable:
    def test_multi_flag_generates_recommendations(self):
        flags = [
            {"severity": "high", "metric": "portfolio_vol_high", "actual_value": 0.28, "threshold": 0.20},
            {"severity": "high", "metric": "max_dd_deep", "actual_value": 0.22, "threshold": 0.15},
        ]
        diag = _make_diagnosis(flags, [
            {"code": "A", "weight_pct": 50, "vol": 0.40, "dd": 0.35, "rec": None, "sharpe": 0.1},
            {"code": "B", "weight_pct": 40, "vol": 0.18, "dd": 0.08, "sharpe": 0.7},
        ])
        returns = _make_returns(["A", "B"])
        result = run_prescription(diag, UserConstraints(existing_cash_pct=10), holding_returns=returns)
        # With random returns, rescore may reject T1 recs -> constrained is also valid
        assert result["recommendations"]["status"] in ("actionable", "constrained")
        # But pipeline should still produce unified shape
        # All items have required fields
        for tier_key in ["tier_1", "tier_2", "tier_3"]:
            for item in result["recommendations"][tier_key]["items"]:
                missing = REQUIRED_REC_KEYS - set(item.keys())
                assert not missing, f"Missing in {tier_key}: {missing}"
                if item.get("rescore"):
                    assert item["rescore"].get("simulation_cases") is not None
                    assert item.get("implementation_plan") is not None
                    assert item.get("summary_plain")


class TestConstrained:
    def test_very_restrictive(self):
        flags = [
            {"severity": "high", "metric": "portfolio_vol_high", "actual_value": 0.28, "threshold": 0.20},
        ]
        diag = _make_diagnosis(flags, [{"code": "A", "weight_pct": 90, "vol": 0.40}])
        # No cash, no new capital, only stocks allowed (no ETF)
        c = UserConstraints(existing_cash_pct=0, additional_capital_ratio="none", allowed_instruments=["stock"])
        result = run_prescription(diag, c)
        assert REQUIRED_TOP_KEYS <= set(result.keys())
        assert result["recommendations"]["status"] in ("actionable", "constrained")

    def test_constrained_has_bottlenecks(self):
        flags = [
            {"severity": "high", "metric": "portfolio_vol_high", "actual_value": 0.28, "threshold": 0.20},
            {"severity": "high", "metric": "max_dd_deep", "actual_value": 0.22, "threshold": 0.15},
        ]
        diag = _make_diagnosis(flags, [{"code": "A", "weight_pct": 95, "vol": 0.45, "dd": 0.35}])
        c = UserConstraints(existing_cash_pct=0, additional_capital_ratio="none", allowed_instruments=["stock"])
        result = run_prescription(diag, c)
        if result["recommendations"]["status"] == "constrained":
            assert isinstance(result["recommendations"]["constraint_bottlenecks"], list)


class TestErrorPaths:
    def test_envelope_rejected(self):
        """run_prescription() rejects full envelope with clear error."""
        envelope = {"status": "ok", "data": _make_diagnosis([], [{"code": "A", "weight_pct": 90}])}
        with pytest.raises(ValueError, match="bare diagnosis data"):
            run_prescription(envelope, UserConstraints())

    def test_missing_section_rejected(self):
        """Missing required section raises DiagnosisSchemaError."""
        from diagnosis_schema import DiagnosisSchemaError
        diag = _make_diagnosis([], [{"code": "A", "weight_pct": 90}])
        del diag["risk_contribution"]
        with pytest.raises(DiagnosisSchemaError, match="Missing required"):
            run_prescription(diag, UserConstraints())


class TestExecStats:
    def test_no_action_has_exec_stats(self):
        diag = _make_diagnosis([], [{"code": "A", "weight_pct": 90}])
        result = run_prescription(diag, UserConstraints())
        assert "_exec_stats" in result
        assert result["_exec_stats"]["rescore_attempted"] == 0
        assert result["_exec_stats"]["status"] == "no_action"

    def test_light_touch_has_exec_stats(self):
        flags = [{"severity": "medium", "metric": "low_sharpe", "actual_value": 0.15, "threshold": 0.50}]
        diag = _make_diagnosis(flags, [{"code": "A", "weight_pct": 90, "sharpe": -0.1}])
        result = run_prescription(diag, UserConstraints())
        assert result["_exec_stats"]["rescore_attempted"] == 0
        assert result["_exec_stats"]["status"] == "light_touch"

    def test_actionable_with_returns_has_rescore(self):
        flags = [{"severity": "high", "metric": "portfolio_vol_high", "actual_value": 0.28, "threshold": 0.20}]
        diag = _make_diagnosis(flags, [
            {"code": "A", "weight_pct": 60, "vol": 0.40},
            {"code": "B", "weight_pct": 30, "vol": 0.18},
        ])
        returns = _make_returns(["A", "B", "511010.SH"])
        result = run_prescription(diag, UserConstraints(existing_cash_pct=10), holding_returns=returns)
        assert result["_exec_stats"]["holding_returns_provided"] is True

    def test_actionable_without_returns_skips_rescore(self):
        flags = [{"severity": "high", "metric": "portfolio_vol_high", "actual_value": 0.28, "threshold": 0.20}]
        diag = _make_diagnosis(flags, [{"code": "A", "weight_pct": 90, "vol": 0.40}])
        result = run_prescription(diag, UserConstraints(existing_cash_pct=10))
        assert result["_exec_stats"]["holding_returns_provided"] is False
        assert result["_exec_stats"]["rescore_attempted"] == 0


class TestDefensiveTrigger:
    def test_gold_triggered_by_hedge(self):
        flags = [
            {"severity": "high", "metric": "portfolio_vol_high", "actual_value": 0.28, "threshold": 0.20},
        ]
        diag = _make_diagnosis(flags, [
            {"code": "A", "weight_pct": 85, "vol": 0.30},
        ])
        h = [Holding(position_name="StockA", ticker="A", weight_pct=85)]
        c = UserConstraints(existing_cash_pct=15, objectives=["hedge"])
        result = run_prescription(diag, c, holdings=h)
        defensive = result.get("asset_alignment", {}).get("defensive", {})
        if defensive:
            gold = next((i for i in defensive.get("items", []) if i["asset_name"] == "黄金"), None)
            if gold:
                assert gold["triggered"] is True


class TestThemeMatching:
    def test_style_tag_matches_theme(self):
        flags = [{"severity": "high", "metric": "portfolio_vol_high", "actual_value": 0.28, "threshold": 0.20}]
        diag = _make_diagnosis(flags, [
            {"code": "300474.SZ", "weight_pct": 50, "name": "景嘉微", "sector": "电子"},
            {"code": "601919.SH", "weight_pct": 40, "name": "中远海控", "sector": "航运"},
        ])
        h = [
            Holding(position_name="景嘉微", ticker="300474.SZ", weight_pct=50, style_tag="CPO"),
            Holding(position_name="中远海控", ticker="601919.SH", weight_pct=40, sector_theme="航运"),
        ]
        c = UserConstraints(existing_cash_pct=10, objectives=["growth"])
        result = run_prescription(diag, c, holdings=h)
        alignment = result.get("asset_alignment", {})
        main = alignment.get("main_theme", {})
        # CPO should match AI硬件
        hit_names = [t["name"] for t in main.get("hit_themes", [])]
        assert "AI硬件" in hit_names or main.get("theme_weight_coverage", 0) > 0


class TestUnifiedShape:
    """All status paths must have identical top-level fields."""

    def _check_shape(self, result):
        assert REQUIRED_TOP_KEYS <= set(result.keys())
        recs = result["recommendations"]
        assert "status" in recs
        assert "message" in recs
        assert "constraint_bottlenecks" in recs
        for tier in ["tier_1", "tier_2", "tier_3"]:
            assert tier in recs
            assert "label" in recs[tier]
            assert "items" in recs[tier]
        assert "total" in result["summary"]
        assert "by_tier" in result["summary"]

    def test_no_action_shape(self):
        diag = _make_diagnosis([], [{"code": "A", "weight_pct": 90}])
        self._check_shape(run_prescription(diag, UserConstraints()))

    def test_light_touch_shape(self):
        flags = [{"severity": "medium", "metric": "low_sharpe", "actual_value": 0.15, "threshold": 0.50}]
        diag = _make_diagnosis(flags, [{"code": "A", "weight_pct": 90, "sharpe": -0.1}])
        self._check_shape(run_prescription(diag, UserConstraints()))

    def test_actionable_shape(self):
        flags = [{"severity": "high", "metric": "portfolio_vol_high", "actual_value": 0.28, "threshold": 0.20}]
        diag = _make_diagnosis(flags, [{"code": "A", "weight_pct": 90, "vol": 0.40}])
        self._check_shape(run_prescription(diag, UserConstraints(existing_cash_pct=10)))


class TestPrescriptionClientOutput:
    def test_client_output_contains_structured_text(self):
        flags = [
            {"severity": "high", "metric": "portfolio_vol_high", "actual_value": 0.28, "threshold": 0.20},
            {"severity": "high", "metric": "max_dd_deep", "actual_value": 0.22, "threshold": 0.15},
        ]
        diag = _make_diagnosis(flags, [
            {"code": "A", "weight_pct": 50, "vol": 0.40, "dd": 0.35, "rec": None, "sharpe": 0.1},
            {"code": "B", "weight_pct": 40, "vol": 0.18, "dd": 0.08, "sharpe": 0.7},
        ])
        returns = _make_returns(["A", "B", "511010.SH"], seed=7)
        result = run_prescription(diag, UserConstraints(existing_cash_pct=10), holding_returns=returns)
        client_output = result["client_output"]
        assert client_output["title"] == "组合优化建议"
        assert "总体判断" in [item["heading"] for item in client_output["sections"]]
        assert result["recommendations"]["message"] in client_output["markdown"]
        assert any(table["title"] == "约束条件" for table in client_output["tables"])
        assert client_output["recommendation_cards"]
        first_card = client_output["recommendation_cards"][0]
        assert first_card["metrics_table"]["title"] == "前后对比"
        assert first_card["metrics_table"]["rows"]
        assert first_card["simulation_table"]["title"] == "两档模拟"


class TestBoundaryScenarios:
    """WA09: boundary scenario tests."""

    def test_missing_risk_contribution_raises(self):
        from diagnosis_schema import DiagnosisSchemaError
        diag = _make_diagnosis([], [{"code": "A", "weight_pct": 90}])
        del diag["risk_contribution"]
        with pytest.raises(DiagnosisSchemaError):
            run_prescription(diag, UserConstraints())

    def test_missing_sector_exposure_raises(self):
        from diagnosis_schema import DiagnosisSchemaError
        diag = _make_diagnosis([], [{"code": "A", "weight_pct": 90}])
        del diag["sector_exposure"]
        with pytest.raises(DiagnosisSchemaError):
            run_prescription(diag, UserConstraints())

    def test_new_format_constraints_only(self):
        diag = _make_diagnosis([], [{"code": "A", "weight_pct": 90}])
        c = UserConstraints(
            allowed_markets=["A-share", "HK"],
            allowed_instruments=["stock", "etf", "option"],
            account_permissions=["option_account"],
        )
        result = run_prescription(diag, c)
        assert result["recommendations"]["status"] == "no_action"

    def test_old_format_constraints_via_prescription_main(self):
        from prescription_main import run_optimization
        diag = _make_diagnosis([], [{"code": "A", "weight_pct": 90}])
        payload = {
            "diagnosis_result": {"status": "ok", "error_message": None, "data": diag},
            "constraints": {
                "investable_markets": ["a_share", "etf"],
                "available_capital": "10-30%",
                "objectives": ["capital_growth"],
            },
        }
        result = run_optimization(payload)
        assert result["status"] == "ok"

    def test_zero_cash_pct_accepted(self):
        diag = _make_diagnosis([], [{"code": "A", "weight_pct": 100}])
        c = UserConstraints(existing_cash_pct=0)
        result = run_prescription(diag, c)
        assert result["recommendations"]["status"] == "no_action"

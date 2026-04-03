"""Tests for prescription_main.py — Phase 3 orchestration layer."""
import pytest
import numpy as np
import pandas as pd

from prescription_main import run_optimization, _validate_internal, InternalValidationError


EXTERNAL_DATA_KEYS = {
    "recommendations", "exclusive_groups", "asset_alignment",
    "constraints_applied", "summary", "client_output", "execution_info",
}


def _make_bare_data(flags=None):
    """Minimal bare diagnosis data dict."""
    return {
        "risk_flags": flags or [],
        "risk_metrics": {
            "portfolio": {"ann_volatility": 0.20, "max_drawdown": 0.15, "sharpe_ratio": 0.5},
            "holdings": [
                {"code": "A", "weight_pct": 60, "ann_volatility": 0.25, "max_drawdown": 0.15,
                 "sharpe_ratio": 0.3, "ann_return_arithmetic": 0.08, "sector_theme": ""},
                {"code": "B", "weight_pct": 30, "ann_volatility": 0.18, "max_drawdown": 0.10,
                 "sharpe_ratio": 0.6, "ann_return_arithmetic": 0.10, "sector_theme": ""},
            ],
        },
        "risk_contribution": {"by_holding": [
            {"code": "A", "weight_normalized": 0.67, "pct_risk_contribution": 0.70},
            {"code": "B", "weight_normalized": 0.33, "pct_risk_contribution": 0.30},
        ]},
        "concentration": {"effective_n": 2, "hhi": 0.30, "max_sector_pct": 0.40},
        "correlation_matrix": {"labels": ["A", "B"], "matrix": [], "high_correlation_pairs": []},
        "factor_exposure": {"portfolio": {}, "holdings": {}},
        "sector_exposure": {"sectors": [], "portfolio": []},
        "liquidity": {"portfolio_max_liquidation_days": 2.0, "holdings": []},
        "benchmark": {"benchmark_code": "000300.SH", "beta": 1.0},
        "intraday_micro": None,
        "metadata": {"annualization_factor": 252, "risk_tolerance": "moderate", "data_frequency": "daily"},
    }


def _make_internal():
    """Minimal valid _internal."""
    dates = ["2025-01-02", "2025-01-03", "2025-01-06"]
    return {
        "holding_returns": {
            "A": {"index": dates, "values": [0.01, -0.005, 0.008]},
            "B": {"index": dates, "values": [0.005, -0.002, 0.006]},
        },
        "benchmark_returns": {"index": dates, "values": [0.003, -0.001, 0.004]},
        "holdings": [
            {"ticker": "A", "position_name": "StockA", "weight_pct": 60.0,
             "vehicle_type": "stock", "asset_class": "equity", "region": "China",
             "sector_theme": "", "style_tag": "", "lookthrough_group": "",
             "risk_role": "", "notes": ""},
            {"ticker": "B", "position_name": "StockB", "weight_pct": 30.0,
             "vehicle_type": "stock", "asset_class": "equity", "region": "China",
             "sector_theme": "", "style_tag": "", "lookthrough_group": "",
             "risk_role": "", "notes": ""},
        ],
        "cash_pct": 10.0,
        "portfolio_market_value": 1000000,
    }


def _make_envelope(flags=None, internal=True):
    """Build full payload for run_optimization."""
    data = _make_bare_data(flags)
    diag = {"status": "ok", "error_message": None, "data": data}
    if internal:
        diag["_internal"] = _make_internal()
    # Simulate run_pipeline() sibling keys
    diag["client_output"] = {"title": "test"}
    diag["artifacts"] = None
    diag["pipeline"] = {"as_of": "2025-04-01"}
    return {
        "diagnosis_result": diag,
        "constraints": {"allowed_markets": ["A-share"], "objectives": ["growth"]},
    }


class TestHappyPath:
    def test_no_action(self):
        result = run_optimization(_make_envelope(flags=[]))
        assert result["status"] == "ok"
        assert EXTERNAL_DATA_KEYS == set(result["data"].keys())
        assert result["data"]["recommendations"]["status"] == "no_action"
        assert "_exec_stats" not in result["data"]

    def test_actionable(self):
        flags = [{"severity": "high", "metric": "portfolio_vol_high", "actual_value": 0.28, "threshold": 0.20}]
        result = run_optimization(_make_envelope(flags=flags))
        assert result["status"] == "ok"
        assert EXTERNAL_DATA_KEYS == set(result["data"].keys())

    def test_execution_info_present(self):
        result = run_optimization(_make_envelope(flags=[]))
        ei = result["data"]["execution_info"]
        assert isinstance(ei["rescore_executed"], bool)
        assert isinstance(ei["stress_test_executed"], bool)
        assert isinstance(ei["warnings"], list)


class TestEnvelopeValidation:
    def test_missing_diagnosis_result(self):
        result = run_optimization({"constraints": {}})
        assert result["status"] == "error"
        assert "diagnosis_result" in result["error_message"]

    def test_status_not_ok(self):
        payload = {
            "diagnosis_result": {"status": "error", "error_message": "phase 2 failed", "data": None},
            "constraints": {},
        }
        result = run_optimization(payload)
        assert result["status"] == "error"
        assert "phase 2 failed" in result["error_message"]

    def test_data_is_null(self):
        payload = {
            "diagnosis_result": {"status": "ok", "error_message": None, "data": None},
            "constraints": {},
        }
        result = run_optimization(payload)
        assert result["status"] == "error"
        assert "data must be" in result["error_message"]

    def test_data_schema_violation(self):
        payload = {
            "diagnosis_result": {
                "status": "ok", "error_message": None,
                "data": {"risk_flags": []},  # missing 10 sections
            },
            "constraints": {},
        }
        result = run_optimization(payload)
        assert result["status"] == "error"
        assert "schema validation" in result["error_message"]


class TestInternalDegradation:
    def test_missing_internal_degrades(self):
        result = run_optimization(_make_envelope(internal=False))
        assert result["status"] == "ok"
        ei = result["data"]["execution_info"]
        assert ei["rescore_executed"] is False
        assert any("_internal missing" in w for w in ei["warnings"])

    def test_half_internal_is_error(self):
        payload = _make_envelope()
        payload["diagnosis_result"]["_internal"] = {"cash_pct": 10.0}  # missing 4 keys
        result = run_optimization(payload)
        assert result["status"] == "error"
        assert "incomplete" in result["error_message"]


class TestInternalValidation:
    def test_index_values_mismatch(self):
        internal = _make_internal()
        internal["holding_returns"]["A"]["values"] = [0.01]  # 1 vs 3 dates
        with pytest.raises(InternalValidationError, match="index length"):
            _validate_internal(internal)

    def test_missing_ticker(self):
        internal = _make_internal()
        internal["holdings"][0] = {"weight_pct": 60.0}  # no ticker
        with pytest.raises(InternalValidationError, match="ticker"):
            _validate_internal(internal)

    def test_negative_cash_pct(self):
        internal = _make_internal()
        internal["cash_pct"] = -5.0
        with pytest.raises(InternalValidationError, match="cash_pct"):
            _validate_internal(internal)

    def test_zero_pmv(self):
        internal = _make_internal()
        internal["portfolio_market_value"] = 0
        with pytest.raises(InternalValidationError, match="portfolio_market_value"):
            _validate_internal(internal)

    def test_null_pmv_ok(self):
        internal = _make_internal()
        internal["portfolio_market_value"] = None
        _validate_internal(internal)  # should not raise


class TestSiblingKeyPassthrough:
    def test_full_pipeline_output_accepted(self):
        """run_pipeline() full output (with client_output/artifacts/pipeline) passes through."""
        result = run_optimization(_make_envelope())
        assert result["status"] == "ok"

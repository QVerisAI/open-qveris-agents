"""Tests for Phase 2 diagnosis output schema validation."""
import pytest

from diagnosis_schema import (
    validate_diagnosis_result,
    validate_diagnosis_data,
    DiagnosisSchemaError,
    REQUIRED_DATA_SECTIONS,
)


def _make_valid_result():
    """Minimal valid diagnosis result."""
    return {
        "status": "ok",
        "error_message": None,
        "data": {
            "correlation_matrix": {
                "labels": ["A", "B"],
                "matrix": [[1.0, 0.5], [0.5, 1.0]],
                "high_correlation_pairs": [],
            },
            "risk_metrics": {
                "holdings": [{"code": "A", "ann_volatility": 0.2}],
                "portfolio": {"ann_volatility": 0.18, "sharpe_ratio": 1.2},
            },
            "risk_contribution": {"by_holding": [], "by_sector": []},
            "concentration": {"hhi": 0.25, "effective_n": 4},
            "benchmark": {"benchmark_code": "000300.SH", "beta": 0.95},
            "factor_exposure": {
                "factor_order": ["size"],
                "holdings": {},
                "portfolio": {"size": 0.3},
            },
            "sector_exposure": {"sectors": [], "portfolio": []},
            "liquidity": {"holdings": [], "portfolio_max_liquidation_days": 3},
            "intraday_micro": None,
            "risk_flags": [
                {"metric": "portfolio_vol_high", "severity": "high",
                 "actual_value": 0.25, "threshold": 0.20},
            ],
            "metadata": {
                "data_frequency": "daily",
                "annualization_factor": 252,
                "risk_tolerance": "moderate",
                "lookback_period": "2y",
                "warnings": [],
            },
        },
    }


class TestValidResult:
    def test_valid_passes(self):
        validate_diagnosis_result(_make_valid_result())

    def test_error_status_passes(self):
        result = {"status": "error", "error_message": "something broke", "data": None}
        validate_diagnosis_result(result)


class TestStrictTopLevel:
    def test_extra_top_key_rejected(self):
        result = _make_valid_result()
        result["_internal"] = {"foo": 1}
        with pytest.raises(DiagnosisSchemaError, match="Unexpected top-level keys"):
            validate_diagnosis_result(result)

    def test_extra_underscore_key_rejected(self):
        result = _make_valid_result()
        result["_holdings_normalized"] = {}
        with pytest.raises(DiagnosisSchemaError, match="Unexpected top-level keys"):
            validate_diagnosis_result(result)


class TestMissingSections:
    def test_missing_risk_contribution(self):
        result = _make_valid_result()
        del result["data"]["risk_contribution"]
        with pytest.raises(DiagnosisSchemaError, match="Missing required data sections"):
            validate_diagnosis_result(result)

    def test_missing_sector_exposure(self):
        result = _make_valid_result()
        del result["data"]["sector_exposure"]
        with pytest.raises(DiagnosisSchemaError, match="Missing required data sections"):
            validate_diagnosis_result(result)

    def test_missing_intraday_micro(self):
        result = _make_valid_result()
        del result["data"]["intraday_micro"]
        with pytest.raises(DiagnosisSchemaError, match="Missing required data sections"):
            validate_diagnosis_result(result)


class TestExtraDataKeys:
    def test_extra_data_key_rejected(self):
        result = _make_valid_result()
        result["data"]["surprise_field"] = 42
        with pytest.raises(DiagnosisSchemaError, match="Unexpected keys in data"):
            validate_diagnosis_result(result)


class TestSectionValidation:
    def test_correlation_matrix_missing_labels(self):
        result = _make_valid_result()
        del result["data"]["correlation_matrix"]["labels"]
        with pytest.raises(DiagnosisSchemaError, match="correlation_matrix missing 'labels'"):
            validate_diagnosis_result(result)

    def test_risk_metrics_missing_portfolio(self):
        result = _make_valid_result()
        del result["data"]["risk_metrics"]["portfolio"]
        with pytest.raises(DiagnosisSchemaError, match="risk_metrics missing 'portfolio'"):
            validate_diagnosis_result(result)

    def test_risk_flags_not_list(self):
        result = _make_valid_result()
        result["data"]["risk_flags"] = "not a list"
        with pytest.raises(DiagnosisSchemaError, match="risk_flags must be list"):
            validate_diagnosis_result(result)

    def test_risk_flag_missing_severity(self):
        result = _make_valid_result()
        result["data"]["risk_flags"] = [{"metric": "test"}]
        with pytest.raises(DiagnosisSchemaError, match="risk_flags.*missing 'severity'"):
            validate_diagnosis_result(result)

    def test_metadata_missing_annualization_factor(self):
        result = _make_valid_result()
        del result["data"]["metadata"]["annualization_factor"]
        with pytest.raises(DiagnosisSchemaError, match="metadata missing"):
            validate_diagnosis_result(result)


class TestValidateDataOnly:
    def test_valid_data(self):
        result = _make_valid_result()
        validate_diagnosis_data(result["data"])

    def test_missing_section(self):
        result = _make_valid_result()
        del result["data"]["liquidity"]
        with pytest.raises(DiagnosisSchemaError, match="Missing required data sections"):
            validate_diagnosis_data(result["data"])


class TestRequiredSectionsConstant:
    def test_count(self):
        assert len(REQUIRED_DATA_SECTIONS) == 11

    def test_includes_risk_contribution(self):
        assert "risk_contribution" in REQUIRED_DATA_SECTIONS

    def test_includes_sector_exposure(self):
        assert "sector_exposure" in REQUIRED_DATA_SECTIONS

    def test_includes_intraday_micro(self):
        assert "intraday_micro" in REQUIRED_DATA_SECTIONS

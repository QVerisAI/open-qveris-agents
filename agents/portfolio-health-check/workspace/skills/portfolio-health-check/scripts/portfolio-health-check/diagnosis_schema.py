"""Phase 2 diagnosis output schema — strict public contract.

Only validates the public contract (status / error_message / data with 11 sections).
Internal fields (_internal, _holdings_normalized, etc.) are NOT part of this schema
and must not appear in run_diagnosis() output.
"""
from __future__ import annotations

from typing import Any

# The 11 required sections under data
REQUIRED_DATA_SECTIONS = frozenset([
    "correlation_matrix",
    "risk_metrics",
    "risk_contribution",
    "concentration",
    "benchmark",
    "factor_exposure",
    "sector_exposure",
    "liquidity",
    "intraday_micro",
    "risk_flags",
    "metadata",
])


class DiagnosisSchemaError(Exception):
    """Raised when diagnosis output violates the public contract."""


def validate_diagnosis_result(result: dict[str, Any]) -> None:
    """Validate run_diagnosis() output against the strict public contract.

    Raises DiagnosisSchemaError if validation fails.
    """
    if not isinstance(result, dict):
        raise DiagnosisSchemaError(f"Expected dict, got {type(result).__name__}")

    # --- Top-level keys ---
    _require_key(result, "status", str)
    _require_key(result, "error_message", (str, type(None)))

    if result["status"] != "ok":
        # Error responses only need status + error_message + data (null)
        return

    _require_key(result, "data", dict)
    data = result["data"]

    # Strict: no unexpected top-level keys
    allowed_top = {"status", "error_message", "data"}
    extra_top = set(result.keys()) - allowed_top
    if extra_top:
        raise DiagnosisSchemaError(
            f"Unexpected top-level keys in diagnosis result: {extra_top}. "
            f"run_diagnosis() must only return {allowed_top}."
        )

    # --- data sections ---
    missing = REQUIRED_DATA_SECTIONS - set(data.keys())
    if missing:
        raise DiagnosisSchemaError(f"Missing required data sections: {missing}")

    extra_data = set(data.keys()) - REQUIRED_DATA_SECTIONS
    if extra_data:
        raise DiagnosisSchemaError(
            f"Unexpected keys in data: {extra_data}. "
            f"Allowed sections: {sorted(REQUIRED_DATA_SECTIONS)}"
        )

    # --- Section-level structural checks ---
    _validate_correlation_matrix(data["correlation_matrix"])
    _validate_risk_metrics(data["risk_metrics"])
    _validate_risk_flags(data["risk_flags"])
    _validate_metadata(data["metadata"])


def validate_diagnosis_data(data: dict[str, Any]) -> None:
    """Validate just the .data portion (used by Phase 3 consumers).

    Checks section presence AND structural integrity of key sections.
    """
    if not isinstance(data, dict):
        raise DiagnosisSchemaError(f"Expected dict for data, got {type(data).__name__}")

    missing = REQUIRED_DATA_SECTIONS - set(data.keys())
    if missing:
        raise DiagnosisSchemaError(f"Missing required data sections: {missing}")

    _validate_correlation_matrix(data["correlation_matrix"])
    _validate_risk_metrics(data["risk_metrics"])
    _validate_risk_flags(data["risk_flags"])
    _validate_metadata(data["metadata"])


# ---------------------------------------------------------------------------
# Section validators
# ---------------------------------------------------------------------------

def _validate_correlation_matrix(cm: Any) -> None:
    if not isinstance(cm, dict):
        raise DiagnosisSchemaError("correlation_matrix must be dict")
    for key in ("labels", "matrix", "high_correlation_pairs"):
        if key not in cm:
            raise DiagnosisSchemaError(f"correlation_matrix missing '{key}'")
    if not isinstance(cm["labels"], list):
        raise DiagnosisSchemaError("correlation_matrix.labels must be list")
    if not isinstance(cm["matrix"], list):
        raise DiagnosisSchemaError("correlation_matrix.matrix must be list")


def _validate_risk_metrics(rm: Any) -> None:
    if not isinstance(rm, dict):
        raise DiagnosisSchemaError("risk_metrics must be dict")
    for key in ("holdings", "portfolio"):
        if key not in rm:
            raise DiagnosisSchemaError(f"risk_metrics missing '{key}'")


def _validate_risk_flags(flags: Any) -> None:
    if not isinstance(flags, list):
        raise DiagnosisSchemaError("risk_flags must be list")
    for i, flag in enumerate(flags):
        if not isinstance(flag, dict):
            raise DiagnosisSchemaError(f"risk_flags[{i}] must be dict")
        for key in ("metric", "severity"):
            if key not in flag:
                raise DiagnosisSchemaError(f"risk_flags[{i}] missing '{key}'")


def _validate_metadata(meta: Any) -> None:
    if not isinstance(meta, dict):
        raise DiagnosisSchemaError("metadata must be dict")
    for key in ("data_frequency", "annualization_factor", "risk_tolerance"):
        if key not in meta:
            raise DiagnosisSchemaError(f"metadata missing '{key}'")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_key(d: dict, key: str, expected_type: type | tuple) -> None:
    if key not in d:
        raise DiagnosisSchemaError(f"Missing required key: '{key}'")
    if not isinstance(d[key], expected_type):
        raise DiagnosisSchemaError(
            f"Key '{key}' must be {expected_type}, got {type(d[key]).__name__}"
        )

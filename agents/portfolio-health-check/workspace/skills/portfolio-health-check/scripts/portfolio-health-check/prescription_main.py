"""Phase 3 orchestration layer — prescription_main.py.

Bridges the gap between API payload and run_prescription().
Handles envelope validation, _internal extraction/degradation,
schema checks, and response wrapping.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import traceback
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from _security import UnsafePathError, open_safely, safe_resolve, scrub_error
from asset_paths import safe_input_roots
from data_loader import Holding, parse_constraints
from diagnosis_schema import validate_diagnosis_data, DiagnosisSchemaError
from prescription import run_prescription


# ---------------------------------------------------------------------------
# _internal validator
# ---------------------------------------------------------------------------

_INTERNAL_REQUIRED_KEYS = frozenset(
    [
        "holding_returns",
        "benchmark_returns",
        "holdings",
        "cash_pct",
        "portfolio_market_value",
    ]
)


class InternalValidationError(Exception):
    """Raised when _internal exists but has invalid structure."""


def _validate_internal(internal: dict) -> None:
    """Validate _internal structure. Raises InternalValidationError on failure."""
    # All-or-nothing: half-present is illegal
    missing = _INTERNAL_REQUIRED_KEYS - set(internal.keys())
    if missing:
        raise InternalValidationError(
            f"_internal is incomplete, missing keys: {missing}. "
            f"Must contain all of: {sorted(_INTERNAL_REQUIRED_KEYS)}"
        )

    # holding_returns
    hr = internal["holding_returns"]
    if not isinstance(hr, dict):
        raise InternalValidationError("_internal.holding_returns must be dict")
    for code, series_dict in hr.items():
        if not isinstance(series_dict, dict):
            raise InternalValidationError(
                f"holding_returns[{code}] must be dict with index/values"
            )
        if "index" not in series_dict or "values" not in series_dict:
            raise InternalValidationError(
                f"holding_returns[{code}] missing index or values"
            )
        if len(series_dict["index"]) != len(series_dict["values"]):
            raise InternalValidationError(
                f"holding_returns[{code}]: index length {len(series_dict['index'])} "
                f"!= values length {len(series_dict['values'])}"
            )
        # Validate dates are parseable
        for d in series_dict["index"][:3]:  # spot-check first few
            try:
                pd.Timestamp(d)
            except (ValueError, TypeError):
                raise InternalValidationError(
                    f"holding_returns[{code}]: unparseable date '{d}'"
                )

    # benchmark_returns (can be null)
    br = internal["benchmark_returns"]
    if br is not None:
        if not isinstance(br, dict):
            raise InternalValidationError(
                "_internal.benchmark_returns must be dict or null"
            )
        if "index" not in br or "values" not in br:
            raise InternalValidationError("benchmark_returns missing index or values")
        if len(br["index"]) != len(br["values"]):
            raise InternalValidationError(
                f"benchmark_returns: index length {len(br['index'])} != values length {len(br['values'])}"
            )

    # holdings
    hl = internal["holdings"]
    if not isinstance(hl, list):
        raise InternalValidationError("_internal.holdings must be list")
    for i, h in enumerate(hl):
        if not isinstance(h, dict):
            raise InternalValidationError(f"holdings[{i}] must be dict")
        if not h.get("ticker"):
            raise InternalValidationError(f"holdings[{i}] missing or empty 'ticker'")
        if not isinstance(h.get("weight_pct"), (int, float)):
            raise InternalValidationError(
                f"holdings[{i}] missing or non-numeric 'weight_pct'"
            )

    # cash_pct
    cp = internal["cash_pct"]
    if not isinstance(cp, (int, float)):
        raise InternalValidationError("_internal.cash_pct must be numeric")
    if cp < 0:
        raise InternalValidationError("_internal.cash_pct must be >= 0")

    # portfolio_market_value (nullable)
    pmv = internal["portfolio_market_value"]
    if pmv is not None:
        if not isinstance(pmv, (int, float)):
            raise InternalValidationError(
                "_internal.portfolio_market_value must be numeric or null"
            )
        if pmv <= 0:
            raise InternalValidationError(
                "_internal.portfolio_market_value must be > 0 when set"
            )


# ---------------------------------------------------------------------------
# Deserializers
# ---------------------------------------------------------------------------


def _deserialize_series(d: dict) -> pd.Series:
    """Deserialize {index, values} back to pd.Series with DatetimeIndex."""
    return pd.Series(d["values"], index=pd.to_datetime(d["index"]))


def _deserialize_holdings(dicts: list[dict]) -> list[Holding]:
    """Deserialize list of dicts back to Holding dataclass instances."""
    result = []
    for d in dicts:
        result.append(
            Holding(
                position_name=d.get("position_name", d.get("ticker", "")),
                ticker=d["ticker"],
                weight_pct=float(d["weight_pct"]),
                vehicle_type=d.get("vehicle_type", "stock"),
                asset_class=d.get("asset_class", "equity"),
                region=d.get("region", "China"),
                sector_theme=d.get("sector_theme", ""),
                style_tag=d.get("style_tag", ""),
                lookthrough_group=d.get("lookthrough_group", ""),
                risk_role=d.get("risk_role", ""),
                notes=d.get("notes", ""),
            )
        )
    return result


# ---------------------------------------------------------------------------
# Execution info builder
# ---------------------------------------------------------------------------


def _build_execution_info(exec_stats: dict | None, warnings: list[str]) -> dict:
    """Build execution_info from _exec_stats side-channel."""
    if exec_stats is None:
        return {
            "rescore_executed": False,
            "stress_test_executed": False,
            "warnings": warnings,
        }
    return {
        "rescore_executed": exec_stats.get("rescore_attempted", 0) > 0,
        "stress_test_executed": exec_stats.get("stress_test_attempted", 0) > 0,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------


def run_optimization(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Run Phase 3 optimization from API payload.

    Args:
        payload: Must contain 'diagnosis_result' and 'constraints'.
            diagnosis_result can be the full run_pipeline() output.

    Returns:
        Envelope: {status, error_message, data}.
    """
    try:
        return _run_optimization_inner(payload)
    except Exception as exc:
        logging.error("Optimization failed: %s\n%s", exc, traceback.format_exc())
        return {
            "status": "error",
            "error_message": scrub_error(exc),
            "data": None,
        }


def _run_optimization_inner(payload: Mapping[str, Any]) -> dict[str, Any]:
    warnings: list[str] = []

    # Step 1: Extract diagnosis_result
    diag = payload.get("diagnosis_result")
    if not isinstance(diag, dict):
        return _error("payload must contain 'diagnosis_result' dict")

    # Step 2: Validate envelope
    status = diag.get("status")
    if status != "ok":
        orig_err = diag.get("error_message", "unknown")
        return _error(f"diagnosis_result status is not ok: {orig_err}")

    data = diag.get("data")
    if not isinstance(data, dict):
        return _error("diagnosis_result.data must be a non-null dict")

    # Step 3: Validate .data with strict schema
    try:
        validate_diagnosis_data(data)
    except DiagnosisSchemaError as e:
        return _error(f"diagnosis_result.data schema validation failed: {e}")

    # Step 4: Handle _internal (optional — degrade if missing)
    internal = diag.get("_internal")
    holding_returns = None
    benchmark_returns = None
    holdings = None
    cash_pct = 0.0
    portfolio_market_value = None

    if internal is not None:
        # Validate structure — half-present or malformed is error
        try:
            _validate_internal(internal)
        except InternalValidationError as e:
            return _error(f"_internal validation failed: {e}")

        # Deserialize
        holding_returns = {
            code: _deserialize_series(sd)
            for code, sd in internal["holding_returns"].items()
        }
        if internal["benchmark_returns"] is not None:
            benchmark_returns = _deserialize_series(internal["benchmark_returns"])
        holdings = _deserialize_holdings(internal["holdings"])
        cash_pct = float(internal["cash_pct"])
        portfolio_market_value = internal["portfolio_market_value"]
    else:
        warnings.append(
            "_internal missing: rescore/stress skipped, cash_pct defaults to 0"
        )

    # Step 5: Parse constraints
    constraints_raw = payload.get("constraints") or {}
    constraint_payload = {
        "constraints": constraints_raw,
        "cash_pct": cash_pct,
        "params": {"portfolio_market_value": portfolio_market_value},
    }
    risk_tolerance = data.get("metadata", {}).get("risk_tolerance", "moderate")
    constraints = parse_constraints(constraint_payload, risk_tolerance=risk_tolerance)

    # Step 6: Run prescription
    result = run_prescription(
        data,
        constraints,
        holding_returns=holding_returns,
        benchmark_returns=benchmark_returns,
        holdings=holdings,
    )

    # Step 7: Build response — extract _exec_stats, build execution_info
    exec_stats = result.pop("_exec_stats", None)
    execution_info = _build_execution_info(exec_stats, warnings)
    result["execution_info"] = execution_info

    return {
        "status": "ok",
        "error_message": None,
        "data": result,
    }


def _error(msg: str) -> dict:
    return {"status": "error", "error_message": msg, "data": None}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Phase 3 portfolio optimization")
    parser.add_argument(
        "--diagnosis",
        required=True,
        help="Path to diagnosis_result.json from Phase 2",
    )
    parser.add_argument(
        "--internal",
        default="",
        help="Path to _internal.json from Phase 2 (optional, enables rescore/stress)",
    )
    parser.add_argument(
        "--constraints",
        default="{}",
        help="JSON string of constraints",
    )
    parser.add_argument(
        "--constraints-file",
        default="",
        help="Path to constraints JSON file (takes precedence over --constraints)",
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="Output directory for optimization_result.json",
    )
    args = parser.parse_args()

    roots = safe_input_roots()
    try:
        with open_safely(args.diagnosis, "r", allowed_roots=roots) as f:
            diagnosis_result = json.load(f)

        if args.internal:
            with open_safely(args.internal, "r", allowed_roots=roots) as f:
                diagnosis_result["_internal"] = json.load(f)

        if args.constraints_file:
            with open_safely(args.constraints_file, "r", allowed_roots=roots) as f:
                constraints = json.load(f)
        else:
            constraints = json.loads(args.constraints)
    except UnsafePathError as exc:
        sys.stderr.write(f"输入路径不安全: {exc}\n")
        raise SystemExit(2)

    payload = {
        "diagnosis_result": diagnosis_result,
        "constraints": constraints,
    }

    result = run_optimization(payload)

    if args.output_dir:
        try:
            out = safe_resolve(args.output_dir, allowed_roots=roots, must_exist=False)
        except UnsafePathError as exc:
            sys.stderr.write(f"输出目录不安全: {exc}\n")
            raise SystemExit(2)
        out.mkdir(parents=True, exist_ok=True)
        with open_safely(out / "optimization_result.json", "w", allowed_roots=roots) as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

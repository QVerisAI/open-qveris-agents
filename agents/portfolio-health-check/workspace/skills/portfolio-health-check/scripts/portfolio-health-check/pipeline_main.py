"""One-click portfolio health-check pipeline.

Flow:
1. Normalize API payload
2. Pull market data from QVeris
3. Run diagnosis with direct dict/DataFrame inputs
4. Return structured client text, with optional HTML/PDF artifacts
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import traceback
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from _security import UnsafePathError, open_safely, safe_resolve, scrub_error
from asset_paths import safe_input_roots

from compute.benchmark import select_benchmark_details
from data_loader import Holding
from date_utils import shift_months, shift_years, format_ymd
from diagnosis import run_diagnosis
from generate_report_html import generate_html
from html_pdf import convert_html_to_pdf
from qveris_client import QVerisClient
from structured_output import build_diagnosis_client_output

STYLE_MAP = {
    "market_timing": "timing_rotation",
    "timing_rotation": "timing_rotation",
    "full_rotation": "full_rotation",
    "constant_mix": "constant_mix",
    "dca": "dca",
    "core_satellite": "core_satellite",
}

SCENARIO_MAP = {
    "intraday": {
        "trading_frequency": "intraday",
        "data_frequency": "15min",
        "lookback_period": "3m",
        "main_interval": "15m",
        "main_lookback_period": "3m",
        "daily_helper_period": "1y",
        "benchmark_period": "1y",
    },
    "weekly": {
        "trading_frequency": "weekly",
        "data_frequency": "daily",
        "lookback_period": "1y",
        "main_interval": "D",
        "main_lookback_period": "1y",
        "daily_helper_period": None,
        "benchmark_period": "1y",
    },
    "monthly": {
        "trading_frequency": "monthly",
        "data_frequency": "daily",
        "lookback_period": "2y",
        "main_interval": "D",
        "main_lookback_period": "2y",
        "daily_helper_period": None,
        "benchmark_period": "2y",
    },
    "quarterly": {
        "trading_frequency": "quarterly",
        "data_frequency": "weekly",
        "lookback_period": "3y",
        "main_interval": "W",
        "main_lookback_period": "3y",
        "daily_helper_period": None,
        "benchmark_period": "3y",
    },
    "buy_and_hold": {
        "trading_frequency": "buy_and_hold",
        "data_frequency": "daily",
        "lookback_period": "5y",
        "main_interval": "D",
        "main_lookback_period": "5y",
        "daily_helper_period": None,
        "benchmark_period": "5y",
    },
}

NEUTRAL_FUNDAMENTAL_COLUMNS = [
    "market_cap",
    "pe_ttm",
    "pb",
    "dividend_yield",
    "roe",
    "debt_to_asset",
    "earnings_stability",
]


def build_pipeline_scenario(params: Mapping[str, Any]) -> dict[str, Any]:
    """Map API params to diagnosis scenario fields."""
    rebalance_frequency = str(params.get("rebalance_frequency", "")).strip()
    if rebalance_frequency not in SCENARIO_MAP:
        raise ValueError(f"Unsupported rebalance_frequency: {rebalance_frequency}")

    position_style = str(params.get("position_style", "")).strip()
    normalized_style = STYLE_MAP.get(position_style)
    if not normalized_style:
        raise ValueError(f"Unsupported position_style: {position_style}")

    risk_tolerance = str(params.get("risk_tolerance", "")).strip()
    investment_horizon = str(params.get("investment_horizon", "")).strip()

    scenario = dict(SCENARIO_MAP[rebalance_frequency])
    scenario.update(
        {
            "name": "api-deep-diagnosis",
            "position_style": normalized_style,
            "risk_tolerance": risk_tolerance,
            "investment_horizon": investment_horizon,
            "portfolio_market_value": params.get("portfolio_market_value"),
        }
    )
    return scenario


def run_pipeline(
    payload: Mapping[str, Any],
    output_dir: str | Path | None = None,
    *,
    client: QVerisClient | None = None,
    as_of: str | None = None,
    include_pdf: bool = False,
    emit_artifacts: bool = False,
) -> dict[str, Any]:
    """Run the full portfolio health-check pipeline from API payload."""
    try:
        _validate_payload(payload)
        qveris = client or QVerisClient()

        scenario = build_pipeline_scenario(payload["params"])
        anchor = _parse_anchor(as_of)
        holdings_frame = _holdings_frame(payload)

        # Separate cash rows before any aggregation
        cash_mask = holdings_frame["code"].apply(_is_cash_code)
        if "vehicle_type" in holdings_frame.columns:
            cash_mask = cash_mask | (
                holdings_frame["vehicle_type"].str.lower() == "cash"
            )
        cash_from_holdings = float(holdings_frame.loc[cash_mask, "weight_pct"].sum())
        holdings_frame = holdings_frame.loc[~cash_mask].copy()

        # Merge duplicate tickers (e.g. 600519 30% + 20% -> 50%)
        holdings_frame = holdings_frame.groupby("code", as_index=False).agg(
            {c: "first" if c != "weight_pct" else "sum" for c in holdings_frame.columns}
        )

        codes = holdings_frame["code"].dropna().astype(str).tolist()

        prices_bundle = _fetch_price_bundle(qveris, codes, scenario, anchor)
        basics = qveris.fetch_company_basics(codes)
        market_caps = qveris.fetch_market_caps(codes)
        fundamentals = _build_fundamentals(holdings_frame, basics, market_caps)

        weights = {
            row["code"]: float(row["weight_pct"]) / 100
            for row in holdings_frame.to_dict("records")
        }
        benchmark_choice = select_benchmark_details(weights, fundamentals)
        benchmark_history = qveris.fetch_history_quotation(
            [benchmark_choice["benchmark_code"]],
            startdate=format_ymd(
                _lookback_start(
                    anchor,
                    SCENARIO_MAP[payload["params"]["rebalance_frequency"]][
                        "benchmark_period"
                    ],
                )
            ),
            enddate=format_ymd(anchor),
            interval="D",
            indicators="close",
        )

        portfolio_payload = _build_portfolio_payload(
            payload, holdings_frame, basics, cash_from_holdings
        )
        captured: dict = {}
        diagnosis_result = run_diagnosis(
            scenario,
            portfolio_payload,
            prices_bundle,
            fundamentals,
            benchmark_history,
            _capture_internals=captured,
        )
        if diagnosis_result["status"] != "ok":
            return diagnosis_result

        diagnosis_result["client_output"] = build_diagnosis_client_output(
            diagnosis_result
        )

        # Build _internal for Phase 3 consumption (before artifacts, so it can be persisted)
        internal_data = _build_internal(captured, payload)
        diagnosis_result["_internal"] = internal_data

        artifact_paths = None
        if emit_artifacts or output_dir is not None or include_pdf:
            artifact_paths = _write_artifacts(
                diagnosis_result,
                output_dir=output_dir,
                include_pdf=include_pdf,
                internal_data=internal_data,
            )
        diagnosis_result["artifacts"] = artifact_paths
        diagnosis_result["pipeline"] = {
            "as_of": format_ymd(anchor),
            "requested_rebalance_frequency": payload["params"]["rebalance_frequency"],
            "selected_benchmark": {
                "benchmark_code": benchmark_choice["benchmark_code"],
                "benchmark_name": benchmark_choice["benchmark_name"],
            },
            "qveris_inputs": {
                "codes": codes,
                "market_cap_coverage": int(fundamentals["market_cap"].notna().sum())
                if "market_cap" in fundamentals
                else 0,
                "has_daily_helper": prices_bundle.get("daily") is not None,
            },
        }
        return diagnosis_result
    except Exception as exc:
        logging.error("Pipeline failed: %s\n%s", exc, traceback.format_exc())
        return {
            "status": "error",
            "error_message": scrub_error(exc),
            "data": None,
            "artifacts": None,
        }


def _validate_payload(payload: Mapping[str, Any]) -> None:
    if (
        "holdings" not in payload
        or not isinstance(payload["holdings"], list)
        or not payload["holdings"]
    ):
        raise ValueError("Payload must include a non-empty holdings list")
    if "params" not in payload or not isinstance(payload["params"], Mapping):
        raise ValueError("Payload must include params")
    required_params = {
        "rebalance_frequency",
        "position_style",
        "risk_tolerance",
        "investment_horizon",
    }
    missing = sorted(required_params - set(payload["params"]))
    if missing:
        raise ValueError(f"Missing required params: {', '.join(missing)}")


def _holdings_frame(payload: Mapping[str, Any]) -> pd.DataFrame:
    df = pd.DataFrame(payload["holdings"]).copy()
    if "code" not in df.columns or "weight_pct" not in df.columns:
        raise ValueError("Each holding must include code and weight_pct")
    df["code"] = df["code"].astype(str)
    df["weight_pct"] = pd.to_numeric(df["weight_pct"], errors="coerce")
    if df["weight_pct"].isna().any():
        raise ValueError("weight_pct must be numeric")
    return df


def _build_portfolio_payload(
    payload: Mapping[str, Any],
    holdings_frame: pd.DataFrame,
    basics: pd.DataFrame,
    cash_from_holdings: float = 0.0,
) -> dict[str, Any]:
    basics_map = {
        row["ticker"]: row
        for _, row in basics.drop_duplicates(subset=["ticker"]).iterrows()
    }
    records: list[dict[str, Any]] = []
    for row in holdings_frame.to_dict("records"):
        meta = basics_map.get(row["code"], {})
        vtype = row.get("vehicle_type") or _infer_vehicle_type(row["code"])
        records.append(
            {
                "code": row["code"],
                "weight_pct": float(row["weight_pct"]),
                "position_name": meta.get("name") or row.get("name") or row["code"],
                "vehicle_type": vtype,
                "asset_class": row.get("asset_class")
                or ("fund" if vtype == "etf" else "equity"),
                "region": row.get("region") or "China",
                "sector_theme": meta.get("sector_theme") or "",
                "style_tag": row.get("style_tag", ""),
                "lookthrough_group": row.get("lookthrough_group", ""),
            }
        )
    # Combine root-level cash_pct with any cash rows extracted from holdings
    root_cash = float(payload.get("cash_pct", 0.0) or 0.0)
    return {
        "holdings": records,
        "cash_pct": root_cash + cash_from_holdings,
    }


def _build_fundamentals(
    holdings_frame: pd.DataFrame,
    basics: pd.DataFrame,
    market_caps: pd.DataFrame,
) -> pd.DataFrame:
    base = pd.DataFrame(
        {"ticker": holdings_frame["code"].astype(str).drop_duplicates()}
    )

    basics_df = (
        basics.drop_duplicates(subset=["ticker"]).copy()
        if not basics.empty
        else pd.DataFrame(columns=["ticker", "name", "sector_theme"])
    )
    market_caps_df = (
        market_caps.drop_duplicates(subset=["ticker"]).copy()
        if not market_caps.empty
        else pd.DataFrame(columns=["ticker", "market_cap", "name"])
    )
    if "name" in market_caps_df.columns:
        market_caps_df = market_caps_df.rename(columns={"name": "market_name"})

    merged = base.merge(basics_df, on="ticker", how="left").merge(
        market_caps_df, on="ticker", how="left"
    )
    if "market_name" in merged.columns:
        merged["name"] = merged["name"].fillna(merged["market_name"])
        merged = merged.drop(columns=["market_name"])

    for col in ("name", "sector_theme"):
        if col not in merged.columns:
            merged[col] = ""
        merged[col] = merged[col].fillna("")

    for col in NEUTRAL_FUNDAMENTAL_COLUMNS:
        if col not in merged.columns:
            merged[col] = pd.NA

    merged["market_cap"] = pd.to_numeric(merged["market_cap"], errors="coerce")
    return merged


def _fetch_price_bundle(
    client: QVerisClient,
    codes: list[str],
    scenario: Mapping[str, Any],
    anchor: date,
) -> dict[str, pd.DataFrame]:
    main_start = _lookback_start(anchor, str(scenario["main_lookback_period"]))
    main_interval = str(scenario["main_interval"])

    if main_interval == "15m":
        main = client.fetch_minute_quotation(
            codes,
            starttime=f"{format_ymd(main_start)} 09:30:00",
            endtime=f"{format_ymd(anchor)} 15:00:00",
            interval="15",
        )
    else:
        main = client.fetch_history_quotation(
            codes,
            startdate=format_ymd(main_start),
            enddate=format_ymd(anchor),
            interval=main_interval,
        )

    bundle: dict[str, pd.DataFrame] = {"main": main}
    daily_helper_period = scenario.get("daily_helper_period")
    if daily_helper_period:
        helper_start = _lookback_start(anchor, str(daily_helper_period))
        bundle["daily"] = client.fetch_history_quotation(
            codes,
            startdate=format_ymd(helper_start),
            enddate=format_ymd(anchor),
            interval="D",
        )
    return bundle


def _write_artifacts(
    diagnosis_result: Mapping[str, Any],
    *,
    output_dir: str | Path | None,
    include_pdf: bool,
    internal_data: dict | None = None,
) -> dict[str, str]:
    artifact_dir = Path(output_dir) if output_dir else _default_output_dir()
    artifact_dir.mkdir(parents=True, exist_ok=True)

    json_path = artifact_dir / "diagnosis_result.json"
    html_path = artifact_dir / "diagnosis_report.html"
    pdf_path = artifact_dir / "diagnosis_report.pdf"
    internal_path = artifact_dir / "_internal.json"

    serializable = {k: v for k, v in diagnosis_result.items() if k != "_internal"}
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(serializable, f, ensure_ascii=False, indent=2)

    if internal_data is not None:
        with open(internal_path, "w", encoding="utf-8") as f:
            json.dump(internal_data, f, ensure_ascii=False, indent=2)

    generate_html(dict(diagnosis_result), html_path)

    artifacts = {
        "output_dir": str(artifact_dir.resolve()),
        "diagnosis_json": str(json_path.resolve()),
        "html_report": str(html_path.resolve()),
    }
    if internal_data is not None:
        artifacts["internal_json"] = str(internal_path.resolve())
    if include_pdf:
        convert_html_to_pdf(html_path, pdf_path)
        artifacts["pdf_report"] = str(pdf_path.resolve())
    return artifacts


def _serialize_series(s: pd.Series) -> dict:
    """Serialize pd.Series to {index, values} for JSON transport."""
    return {
        "index": [str(idx) for idx in s.index],
        "values": [float(v) for v in s.values],
    }


def _serialize_holding(h: Holding) -> dict:
    """Serialize Holding dataclass to dict with all fields."""
    return {
        "position_name": h.position_name,
        "ticker": h.ticker,
        "weight_pct": h.weight_pct,
        "vehicle_type": h.vehicle_type,
        "asset_class": h.asset_class,
        "region": h.region,
        "sector_theme": h.sector_theme,
        "style_tag": h.style_tag,
        "lookthrough_group": h.lookthrough_group,
        "risk_role": h.risk_role,
        "notes": h.notes,
    }


def _build_internal(captured: dict, payload: Mapping[str, Any]) -> dict:
    """Build _internal dict for Phase 3 consumption."""
    holding_returns = captured.get("holding_returns") or {}
    benchmark_returns = captured.get("benchmark_returns")
    holdings = captured.get("holdings") or []
    cash_pct = captured.get("cash_pct", 0.0)

    params = payload.get("params") or {}
    pmv = params.get("portfolio_market_value")

    return {
        "holding_returns": {
            code: _serialize_series(series) for code, series in holding_returns.items()
        },
        "benchmark_returns": _serialize_series(benchmark_returns)
        if benchmark_returns is not None
        else None,
        "holdings": [_serialize_holding(h) for h in holdings],
        "cash_pct": float(cash_pct),
        "portfolio_market_value": float(pmv) if pmv is not None else None,
    }


def _is_cash_code(code: str) -> bool:
    """Detect cash-like holdings by code or vehicle_type."""
    return code.upper() in ("CASH", "现金", "—", "-", "")


def _infer_vehicle_type(code: str) -> str:
    local_code = code.split(".")[0]
    if len(local_code) == 6 and local_code.startswith(("1", "5")):
        return "etf"
    return "stock"


def _parse_anchor(as_of: str | None) -> date:
    if as_of:
        return datetime.strptime(as_of, "%Y-%m-%d").date()
    return date.today()


def _lookback_start(anchor: date, lookback_period: str) -> date:
    if lookback_period.endswith("m"):
        return shift_months(anchor, int(lookback_period[:-1]))
    if lookback_period.endswith("y"):
        return shift_years(anchor, int(lookback_period[:-1]))
    raise ValueError(f"Unsupported lookback_period: {lookback_period}")


def _default_output_dir() -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path(__file__).resolve().parent / "output" / f"run_{ts}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run one-click portfolio health-check pipeline"
    )
    parser.add_argument("payload_file", help="Input payload JSON path")
    parser.add_argument("--output-dir", default="", help="Artifact output directory")
    parser.add_argument("--as-of", default="", help="Anchor date in YYYY-MM-DD")
    parser.add_argument(
        "--pdf", action="store_true", help="Generate PDF in addition to HTML"
    )
    parser.add_argument(
        "--emit-artifacts",
        action="store_true",
        help="Generate JSON/HTML artifacts even when output_dir is not provided",
    )
    args = parser.parse_args()

    roots = safe_input_roots()
    try:
        with open_safely(args.payload_file, "r", allowed_roots=roots) as f:
            payload = json.load(f)
        output_dir = (
            str(safe_resolve(args.output_dir, allowed_roots=roots, must_exist=False))
            if args.output_dir
            else None
        )
    except UnsafePathError as exc:
        sys.stderr.write(f"输入路径不安全: {exc}\n")
        raise SystemExit(2)

    result = run_pipeline(
        payload,
        output_dir=output_dir,
        as_of=args.as_of or None,
        include_pdf=args.pdf,
        emit_artifacts=args.emit_artifacts,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

"""
Diagnosis orchestrator — wires all 15 steps together.

Entry point:
- legacy file-based: run_diagnosis(scenario_path, portfolio_path, prices_dir, fundamentals_path, benchmark_path)
- direct objects: run_diagnosis(scenario_dict, portfolio_df_or_dict, prices_df_or_bundle, fundamentals_df, benchmark_df)
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from data_loader import (
    load_scenario, load_portfolio, load_and_normalize_portfolio,
    load_prices, load_fundamentals, load_benchmark,
    truncate_by_lookback, FREQ_CONFIG, LOOKBACK_BARS, LOOKBACK_DAILY_BARS,
)
from compute.thresholds import validate_params, get_thresholds
from compute.returns import compute_returns
from compute.correlation import compute_correlation_matrix, flag_high_correlations
from compute.risk_metrics import compute_all_metrics
from compute.risk_contribution import compute_risk_contribution
from compute.concentration import compute_all_concentration, compute_sector_exposure
from compute.benchmark import (
    select_benchmark_details,
    compute_benchmark_metrics,
    resample_to_weekly,
)
from compute.factor_engine import compute_all_factors
from compute.factor_exposure import compute_portfolio_factor_exposure
from compute.liquidity import compute_liquidity, compute_intraday_micro
from compute.risk_flags import evaluate_flags
from compute.style_adjuster import adjust_flags


# Price file mapping
PRICE_FILES = {
    "daily": "history_data_daily.csv",
    "weekly": "history_data_weekly.csv",
    "15min": "high_frequency_30min.csv",  # sample is 30min, treated as 15min
}


def run_diagnosis(
    scenario_input: str | Path | Mapping[str, Any],
    portfolio_input: str | Path | pd.DataFrame | list[dict[str, Any]] | Mapping[str, Any],
    prices_input: str | Path | pd.DataFrame | list[dict[str, Any]] | Mapping[str, Any],
    fundamentals_input: str | Path | pd.DataFrame | list[dict[str, Any]] | Mapping[str, Any],
    benchmark_input: str | Path | pd.DataFrame | list[dict[str, Any]] | Mapping[str, Any],
    *,
    _capture_internals: dict | None = None,
) -> dict:
    """Run the full 15-step diagnosis pipeline.

    Supported input shapes:
    - `scenario_input`: JSON path or scenario dict with the diagnosis fields
    - `portfolio_input`: CSV path, DataFrame, records list, or `{holdings, cash_pct}`
    - `prices_input`: prices dir path, single prices DataFrame, or `{"main": ..., "daily": ...}`
    - `fundamentals_input`: CSV path or DataFrame/records
    - `benchmark_input`: CSV path or long-table DataFrame/records
    """

    warnings: list[str] = []

    # ── Step 1: Load scenario ───────────────────────────────────────
    scenario = load_scenario(scenario_input)

    # ── Step 2: Load portfolio ──────────────────────────────────────
    holdings, cash_pct, norm_warnings = load_and_normalize_portfolio(portfolio_input)
    warnings.extend(norm_warnings)

    weights = {h.ticker: h.weight_pct / 100 for h in holdings}
    cash_frac = cash_pct / 100

    # ── Step 3: Validate params ─────────────────────────────────────
    validation = validate_params(
        scenario.position_style, scenario.trading_frequency,
        scenario.data_frequency, scenario.risk_tolerance,
        scenario.investment_horizon,
    )
    if not validation.valid:
        return {"status": "error", "error_message": validation.error, "data": None}
    if validation.warnings:
        warnings.extend(validation.warnings)

    macro_ann_factor = FREQ_CONFIG[scenario.data_frequency]["macro_ann_factor"]

    # ── Step 4: Load data ───────────────────────────────────────────
    main_prices, daily_prices_df = _resolve_price_inputs(
        prices_input, scenario.data_frequency,
    )
    fundamentals = load_fundamentals(fundamentals_input)
    needs_daily_helper = scenario.data_frequency != "daily"
    if needs_daily_helper and daily_prices_df is None:
        warnings.append("非日频场景未提供daily辅助数据，部分指标将退化为主频口径")

    # ── Step 5: Truncate by lookback ────────────────────────────────
    key = (scenario.data_frequency, scenario.lookback_period)
    main_bars = LOOKBACK_BARS.get(key)
    if main_bars:
        main_prices = truncate_by_lookback(main_prices, main_bars)

    if daily_prices_df is not None:
        daily_bars = LOOKBACK_DAILY_BARS.get(scenario.lookback_period)
        if daily_bars:
            daily_prices_df = truncate_by_lookback(daily_prices_df, daily_bars)

    # Split into per-code series
    def _split(df):
        prices_by = {}
        volumes_by = {}
        for code, grp in df.groupby("code"):
            s = grp.set_index("datetime").sort_index()
            prices_by[code] = s["close"]
            volumes_by[code] = s["volume"]
        return prices_by, volumes_by

    main_prices_by, main_volumes_by = _split(main_prices)
    daily_prices_by = None
    daily_volumes_by = None
    if daily_prices_df is not None:
        daily_prices_by, daily_volumes_by = _split(daily_prices_df)

    # ── Filter weights to codes with price data, redistribute missing ──
    missing_codes = [c for c in weights if c not in main_prices_by]
    if missing_codes:
        missing_sum = sum(weights[c] for c in missing_codes)
        weights = {c: w for c, w in weights.items() if c in main_prices_by}
        remaining_sum = sum(weights.values())
        if remaining_sum > 0:
            scale = (remaining_sum + missing_sum) / remaining_sum
            weights = {c: w * scale for c, w in weights.items()}
        warnings.append(
            f"以下标的无价格数据，其权重({missing_sum:.1%})已按比例分配至其余持仓: "
            + ", ".join(missing_codes)
        )

    # For factors, always use daily
    factor_prices = daily_prices_by if daily_prices_by else main_prices_by
    factor_volumes = daily_volumes_by if daily_volumes_by else main_volumes_by

    data_points = min(len(s) for s in main_prices_by.values()) if main_prices_by else 0
    if data_points < 30:
        warnings.append(f"数据点仅{data_points}个，结果可能不可靠")

    if not main_prices_by:
        return {"status": "error", "error_message": "价格数据为空，无法诊断", "data": None}

    first_code = next(iter(main_prices_by))
    date_range = [
        str(main_prices_by[first_code].index.min().date()),
        str(main_prices_by[first_code].index.max().date()),
    ]

    # ── Step 6: Returns ─────────────────────────────────────────────
    returns_result = compute_returns(
        main_prices_by, weights, cash_frac,
        scenario.position_style, scenario.trading_frequency,
        scenario.data_frequency, daily_prices_by,
    )
    warnings.extend(returns_result["warnings"])

    holding_returns = returns_result["holding_returns"]
    portfolio_returns = returns_result["portfolio_returns"]
    eop_weights = returns_result["end_of_period_weights"]
    macro_af = returns_result["macro_ann_factor"]

    # ── Step 7: Correlation ─────────────────────────────────────────
    corr_matrix = compute_correlation_matrix(holding_returns)
    base_thresh = get_thresholds(scenario.risk_tolerance)
    corr_threshold = base_thresh["high_correlation"] or 0.7
    high_pairs = flag_high_correlations(corr_matrix, corr_threshold)

    # ── Step 8: Risk metrics ────────────────────────────────────────
    holding_nav_daily = returns_result.get("holding_nav_daily")
    portfolio_nav_daily = returns_result.get("portfolio_nav_daily")

    risk_holdings = []
    for h in holdings:
        code = h.ticker
        r = holding_returns.get(code)
        if r is None:
            continue
        nav = holding_nav_daily.get(code) if holding_nav_daily else None
        m = compute_all_metrics(r, macro_af, nav_daily=nav)
        m["code"] = code
        m["name"] = h.position_name
        m["weight_pct"] = h.weight_pct
        risk_holdings.append(m)

    portfolio_metrics = compute_all_metrics(
        portfolio_returns, macro_af, nav_daily=portfolio_nav_daily,
    )

    # ── Step 9: Risk contribution ───────────────────────────────────
    risk_contrib = compute_risk_contribution(holding_returns, eop_weights, holdings)

    # ── Step 10: Concentration ──────────────────────────────────────
    concentration = compute_all_concentration(holdings)
    sector_exposure = compute_sector_exposure(holdings)

    # ── Step 11: Benchmark ──────────────────────────────────────────
    benchmark_choice = select_benchmark_details(weights, fundamentals)
    benchmark_code = benchmark_choice["benchmark_code"]
    bench_df = load_benchmark(benchmark_input, benchmark_code)
    benchmark_freq_note = None

    if len(bench_df) > 0:
        if scenario.data_frequency == "weekly":
            bench_df = resample_to_weekly(bench_df)
        elif scenario.data_frequency == "15min":
            benchmark_freq_note = "15min场景，benchmark指标基于日频数据"

        bench_returns = bench_df.set_index("datetime")["close"].pct_change()

        benchmark_metrics = compute_benchmark_metrics(
            portfolio_returns, bench_returns, macro_af,
        )
    else:
        benchmark_metrics = {k: None for k in ["beta", "alpha_annual", "tracking_error", "information_ratio", "relative_max_drawdown"]}
        warnings.append("基准数据缺失，跳过 benchmark 板块")

    benchmark_result = {
        **benchmark_choice,
        **benchmark_metrics,
    }

    # ── Step 12: Factors ────────────────────────────────────────────
    factor_scores, momentum_window = compute_all_factors(
        factor_prices, factor_volumes, fundamentals,
    )
    factor_exposure = compute_portfolio_factor_exposure(factor_scores, weights, cash_frac)

    # ── Step 13: Sector exposure (already computed in step 10) ──────

    # ── Step 14: Liquidity ──────────────────────────────────────────
    liq_volumes = daily_volumes_by if daily_volumes_by else main_volumes_by
    liq_closes = daily_prices_by if daily_prices_by else main_prices_by
    liquidity = compute_liquidity(liq_volumes, liq_closes, weights, scenario.portfolio_market_value)

    # TODO: wire compute_intraday_micro when 15min datetimes are available
    intraday_micro = None

    # ── Step 15: Risk flags + style adjustment ──────────────────────
    flags = evaluate_flags(
        portfolio_metrics, high_pairs,
        factor_exposure["portfolio"], concentration,
        scenario.risk_tolerance, scenario.investment_horizon,
    )
    flags = adjust_flags(flags, scenario.position_style, holdings, warnings)

    # Informational warnings
    if portfolio_metrics.get("max_dd_recovery_days") is None and portfolio_metrics["max_drawdown"] > 0:
        warnings.append("组合最大回撤截至数据末尾尚未恢复")

    max_liq_days = liquidity.get("portfolio_max_liquidation_days")
    if max_liq_days is not None and max_liq_days > 5:
        warnings.append(f"最难卖出的持仓预计需要{max_liq_days:.1f}个交易日清仓，流动性偏紧")

    if benchmark_freq_note:
        warnings.append(benchmark_freq_note)

    # Daily helper window mismatch
    daily_helper_window = None
    if needs_daily_helper and daily_prices_by:
        daily_len = min(len(s) for s in daily_prices_by.values())
        main_len = data_points
        if daily_len < main_len:
            daily_helper_window = f"daily辅助仅{daily_len}bars，短于主频{main_len}bars"
            warnings.append(daily_helper_window)

    # ── Capture internals for Phase 3 (optional) ────────────────────
    if _capture_internals is not None:
        _capture_internals["holding_returns"] = holding_returns
        _capture_internals["benchmark_returns"] = bench_returns if len(bench_df) > 0 else None
        _capture_internals["holdings"] = holdings
        _capture_internals["cash_pct"] = cash_pct

    # ── Assemble output ─────────────────────────────────────────────
    now = datetime.now(timezone(timedelta(hours=8)))

    return {
        "status": "ok",
        "error_message": None,
        "data": {
            "correlation_matrix": {
                "labels": list(corr_matrix.columns),
                "matrix": [[round(v, 2) for v in row] for row in corr_matrix.values],
                "high_correlation_pairs": high_pairs,
            },
            "risk_metrics": {
                "holdings": risk_holdings,
                "portfolio": portfolio_metrics,
            },
            "risk_contribution": risk_contrib,
            "concentration": concentration,
            "benchmark": benchmark_result,
            "factor_exposure": factor_exposure,
            "sector_exposure": sector_exposure,
            "liquidity": liquidity,
            "intraday_micro": intraday_micro,
            "risk_flags": flags,
            "metadata": {
                "data_frequency": scenario.data_frequency,
                "lookback_period": scenario.lookback_period,
                "annualization_factor": macro_af,
                "data_points": data_points,
                "date_range": date_range,
                "trading_frequency": scenario.trading_frequency,
                "risk_tolerance": scenario.risk_tolerance,
                "position_style": scenario.position_style,
                "investment_horizon": scenario.investment_horizon,
                "horizon_adjustment": {
                    "<1y": 0.8, "1-3y": 1.0, "3-5y": 1.2, ">5y": 1.5,
                }.get(scenario.investment_horizon, 1.0),
                "portfolio_return_model": _return_model(scenario.position_style),
                "return_method": "simple",
                "risk_free_rate": 0.015,
                "factor_method": "absolute_anchor",
                "factor_momentum_window": momentum_window,
                "benchmark_code": benchmark_code,
                "benchmark_selection_rule": benchmark_choice["selection_rule"],
                "weighted_avg_market_cap": benchmark_choice["weighted_avg_market_cap"],
                "benchmark_freq_note": benchmark_freq_note,
                "daily_helper_window": daily_helper_window,
                "warnings": warnings,
                "computed_at": now.isoformat(),
            },
        },
    }


def _return_model(style: str) -> str:
    if style == "constant_mix":
        return "constant_mix"
    if style == "timing_rotation":
        return "drift"
    if style == "dca":
        return "periodic_rebalance_approximate"
    return "periodic_rebalance"


def _resolve_price_inputs(
    prices_input: str | Path | pd.DataFrame | list[dict[str, Any]] | Mapping[str, Any],
    data_frequency: str,
) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    """Resolve prices input to (main_prices_df, daily_helper_df)."""
    if isinstance(prices_input, (str, Path)):
        path = Path(prices_input)
        if path.is_dir():
            main_prices = load_prices(path / PRICE_FILES[data_frequency])
            daily_prices = None
            if data_frequency != "daily":
                daily_path = path / "history_data_daily.csv"
                if daily_path.exists():
                    daily_prices = load_prices(daily_path)
            return main_prices, daily_prices
        return load_prices(path), None

    if isinstance(prices_input, Mapping) and ("main" in prices_input or "daily" in prices_input):
        main_source = prices_input.get("main")
        if main_source is None:
            raise ValueError("prices_input dict must include a non-empty 'main' dataset")
        main_prices = load_prices(main_source)
        daily_source = prices_input.get("daily")
        daily_prices = load_prices(daily_source) if daily_source is not None else None
        return main_prices, daily_prices

    return load_prices(prices_input), None

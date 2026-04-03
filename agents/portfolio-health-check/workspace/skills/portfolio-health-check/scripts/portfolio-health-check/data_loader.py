"""
Data loader for portfolio health check.

Reads portfolio CSV, scenario JSON, price CSVs, fundamental CSV, benchmark CSV.
Normalizes column names and dtypes.
Also provides UserConstraints for Phase 3 prescription engine.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import pandas as pd


# ---------------------------------------------------------------------------
# ScenarioParams
# ---------------------------------------------------------------------------

@dataclass
class ScenarioParams:
    trading_frequency: str       # metadata; also drives rebalance interval for periodic styles
    data_frequency: str          # drives price file + macro_ann_factor
    lookback_period: str         # drives data truncation
    position_style: str
    risk_tolerance: str
    investment_horizon: str
    name: str = ""
    portfolio_file: str = ""
    portfolio_market_value: float | None = None  # total portfolio value in CNY, for liquidity


# ---------------------------------------------------------------------------
# Holding
# ---------------------------------------------------------------------------

@dataclass
class Holding:
    position_name: str
    ticker: str
    weight_pct: float
    vehicle_type: str = "stock"
    asset_class: str = "equity"
    region: str = "China"
    sector_theme: str = ""
    style_tag: str = ""
    lookthrough_group: str = ""
    risk_role: str = ""
    notes: str = ""

    @property
    def is_cash(self) -> bool:
        return self.vehicle_type == "cash"


# ---------------------------------------------------------------------------
# UserConstraints (Phase 3)
# ---------------------------------------------------------------------------

CAPITAL_LEVELS: dict[str, float] = {
    "none": 0.0,
    "10-30%": 0.20,
    "30-50%": 0.40,
    "50%+": 0.60,
}

TOLERANCE_TO_OBJECTIVES: dict[str, list[str]] = {
    "conservative": ["income", "hedge"],
    "moderate": ["growth", "income"],
    "aggressive": ["growth"],
    "very_aggressive": ["growth"],
}

# Old API -> new API field migration
_API_CONSTRAINT_MIGRATION: dict[str, dict[str, str]] = {
    "a_share": {"allowed_markets": "A-share", "allowed_exposure": "A-share"},
    "etf": {"allowed_instruments": "etf"},
    "futures": {"allowed_instruments": "futures", "account_permissions": "futures_account"},
    "options": {"allowed_instruments": "option", "account_permissions": "option_account"},
    "hk_connect": {"allowed_markets": "HK", "allowed_exposure": "HK", "account_permissions": "hk_connect"},
    "us_stock": {"allowed_markets": "US", "allowed_exposure": "US", "account_permissions": "qdii"},
    "crypto": {"allowed_instruments": "crypto"},
}

_OBJECTIVES_MIGRATION: dict[str, str] = {
    "capital_growth": "growth",
    "stable_cashflow": "income",
    "hedge_existing_risk": "hedge",
    "ipo_ballast": "ipo_base",
}


@dataclass
class UserConstraints:
    # Four-axis investable scope
    allowed_markets: list[str] = field(default_factory=lambda: ["A-share"])
    allowed_exposure: list[str] = field(default_factory=lambda: ["A-share", "global"])
    allowed_instruments: list[str] = field(default_factory=lambda: ["stock", "etf"])
    account_permissions: list[str] = field(default_factory=list)

    # Funding
    existing_cash_pct: float = 0.0  # from pipeline payload cash_pct (0-100)
    additional_capital_ratio: str = "none"  # "none" | "10-30%" | "30-50%" | "50%+"
    portfolio_market_value: float | None = None

    # Investment objectives (multi-select)
    objectives: list[str] = field(default_factory=lambda: ["growth"])

    # Scenario context (from Phase 2)
    risk_tolerance: str = "moderate"


def parse_constraints(
    payload: Mapping[str, Any],
    risk_tolerance: str = "moderate",
) -> UserConstraints:
    """Parse constraints from API payload. Supports both old and new format."""
    raw = payload.get("constraints") or {}
    cash_pct = float(payload.get("cash_pct", 0.0) or 0.0)
    pmv = None
    params = payload.get("params") or {}
    if "portfolio_market_value" in params:
        pmv = params["portfolio_market_value"]

    # New format detection
    if "allowed_markets" in raw:
        am = raw["allowed_markets"]
        ae = raw.get("allowed_exposure", am + ["global"] if "global" not in am else am)
        return UserConstraints(
            allowed_markets=am,
            allowed_exposure=ae,
            allowed_instruments=raw.get("allowed_instruments", ["stock", "etf"]),
            account_permissions=raw.get("account_permissions", []),
            existing_cash_pct=cash_pct,
            additional_capital_ratio=raw.get("additional_capital_ratio", "none"),
            portfolio_market_value=pmv,
            objectives=raw.get("objectives", TOLERANCE_TO_OBJECTIVES.get(risk_tolerance, ["growth"])),
            risk_tolerance=risk_tolerance,
        )

    # Old format migration
    if "investable_markets" in raw:
        markets: set[str] = set()
        exposure: set[str] = set()
        instruments: set[str] = {"stock"}  # stock always allowed
        permissions: set[str] = set()

        for item in raw["investable_markets"]:
            migration = _API_CONSTRAINT_MIGRATION.get(item, {})
            if "allowed_markets" in migration:
                markets.add(migration["allowed_markets"])
            if "allowed_exposure" in migration:
                exposure.add(migration["allowed_exposure"])
            if "allowed_instruments" in migration:
                instruments.add(migration["allowed_instruments"])
            if "account_permissions" in migration:
                permissions.add(migration["account_permissions"])

        if not markets:
            markets.add("A-share")
        exposure.add("global")

        old_objectives = raw.get("objectives", [])
        objectives = [_OBJECTIVES_MIGRATION.get(o, o) for o in old_objectives]
        if not objectives:
            objectives = TOLERANCE_TO_OBJECTIVES.get(risk_tolerance, ["growth"])

        old_capital = raw.get("available_capital", "none")
        capital_map = {"none": "none", "10-30%": "10-30%", "30-50%": "30-50%", ">50%": "50%+"}

        return UserConstraints(
            allowed_markets=sorted(markets),
            allowed_exposure=sorted(exposure),
            allowed_instruments=sorted(instruments),
            account_permissions=sorted(permissions),
            existing_cash_pct=cash_pct,
            additional_capital_ratio=capital_map.get(old_capital, "none"),
            portfolio_market_value=pmv,
            objectives=objectives,
            risk_tolerance=risk_tolerance,
        )

    # No constraints at all -> defaults
    return UserConstraints(
        existing_cash_pct=cash_pct,
        portfolio_market_value=pmv,
        objectives=TOLERANCE_TO_OBJECTIVES.get(risk_tolerance, ["growth"]),
        risk_tolerance=risk_tolerance,
    )


# ---------------------------------------------------------------------------
# Data frequency config
# ---------------------------------------------------------------------------

FREQ_CONFIG = {
    "daily": {"macro_ann_factor": 252},
    "weekly": {"macro_ann_factor": 52},
    "15min": {"macro_ann_factor": 252, "intraday_ann_factor": 4032},
}

# lookback_period -> daily bar count (used for truncation + auxiliary daily)
LOOKBACK_DAILY_BARS = {
    "2m": 42, "3m": 63,
    "1y": 252, "2y": 504, "3y": 756, "5y": 1260,
}

# lookback_period x data_frequency -> main-frequency bar count
LOOKBACK_BARS = {
    ("daily", "2m"): 42, ("daily", "3m"): 63,
    ("daily", "1y"): 252, ("daily", "2y"): 504,
    ("daily", "3y"): 756, ("daily", "5y"): 1260,
    ("weekly", "1y"): 52, ("weekly", "2y"): 104,
    ("weekly", "3y"): 156, ("weekly", "5y"): 260,
    ("15min", "2m"): 672, ("15min", "3m"): 1008,
    ("15min", "1y"): 4032,
}


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def _scenario_from_mapping(data: Mapping[str, Any]) -> ScenarioParams:
    """Normalize a scenario mapping into ScenarioParams."""
    # Normalize data_frequency: accept "30min" as alias for "15min"
    df = data["data_frequency"]
    if df == "30min":
        df = "15min"

    return ScenarioParams(
        name=data.get("name", ""),
        portfolio_file=data.get("portfolio", ""),
        trading_frequency=data["trading_frequency"],
        data_frequency=df,
        lookback_period=data["lookback_period"],
        position_style=data["position_style"],
        risk_tolerance=data["risk_tolerance"],
        investment_horizon=data["investment_horizon"],
        portfolio_market_value=data.get("portfolio_market_value"),
    )


def load_scenario(source: str | Path | Mapping[str, Any] | ScenarioParams) -> ScenarioParams:
    """Load scenario from JSON path, dict-like object, or ScenarioParams."""
    if isinstance(source, ScenarioParams):
        return source
    if isinstance(source, Mapping):
        return _scenario_from_mapping(source)

    with open(source, encoding="utf-8") as f:
        data = json.load(f)
    return _scenario_from_mapping(data)


def _coerce_frame(
    source: pd.DataFrame | list[dict[str, Any]] | Mapping[str, Any],
    label: str,
) -> pd.DataFrame:
    """Convert records-like input to DataFrame."""
    if isinstance(source, pd.DataFrame):
        return source.copy()
    if isinstance(source, list):
        return pd.DataFrame(source)
    if isinstance(source, Mapping):
        return pd.DataFrame(source)
    raise TypeError(f"Unsupported {label} input type: {type(source)!r}")


def _portfolio_from_frame(
    df: pd.DataFrame,
    cash_pct_override: float | None = None,
) -> tuple[list[Holding], float]:
    """Normalize a portfolio DataFrame -> (holdings list, cash_pct)."""
    if df.empty and cash_pct_override is None:
        return [], 0.0

    df = df.copy()
    if "ticker" not in df.columns and "code" in df.columns:
        df = df.rename(columns={"code": "ticker"})
    if "position_name" not in df.columns:
        if "name" in df.columns:
            df = df.rename(columns={"name": "position_name"})
        else:
            df["position_name"] = df.get("ticker", "")
    if "weight_pct" not in df.columns:
        raise ValueError("Portfolio input must include weight_pct")

    if "vehicle_type" not in df.columns:
        df["vehicle_type"] = "stock"
    if "asset_class" not in df.columns:
        df["asset_class"] = "equity"
    if "region" not in df.columns:
        df["region"] = "China"

    holdings: list[Holding] = []
    cash_pct = float(cash_pct_override or 0.0)

    for _, row in df.iterrows():
        h = Holding(
            position_name=str(row.get("position_name", "") or row.get("ticker", "")),
            ticker=str(row.get("ticker", "")),
            weight_pct=float(row["weight_pct"]),
            vehicle_type=str(row.get("vehicle_type", "stock")),
            asset_class=str(row.get("asset_class", "equity")),
            region=str(row.get("region", "China")),
            sector_theme=str(row.get("sector_theme", "")),
            style_tag=str(row.get("style_tag", "")),
            lookthrough_group=str(row.get("lookthrough_group", "")),
            risk_role=str(row.get("risk_role", "")),
            notes=str(row.get("notes", "")),
        )
        if h.is_cash:
            cash_pct += h.weight_pct
        else:
            holdings.append(h)

    return holdings, cash_pct


def load_portfolio(
    source: str | Path | pd.DataFrame | list[dict[str, Any]] | Mapping[str, Any],
) -> tuple[list[Holding], float]:
    """Load portfolio CSV, DataFrame, or records -> (holdings list, cash_pct).

    Returns non-cash holdings and cash_pct separately.
    """
    if isinstance(source, (str, Path)):
        df = pd.read_csv(source)
        return _portfolio_from_frame(df)

    if isinstance(source, Mapping) and "holdings" in source:
        df = _coerce_frame(source.get("holdings", []), "portfolio holdings")
        cash_pct = source.get("cash_pct")
        return _portfolio_from_frame(
            df,
            cash_pct_override=float(cash_pct) if cash_pct is not None else None,
        )

    df = _coerce_frame(source, "portfolio")
    return _portfolio_from_frame(df)


def load_and_normalize_portfolio(
    source: str | Path | pd.DataFrame | list[dict[str, Any]] | Mapping[str, Any],
) -> tuple[list[Holding], float, list[str]]:
    """Load portfolio, merge duplicate tickers, and normalize weights to 100%.

    Returns (holdings, cash_pct, warnings).
    """
    holdings, cash_pct = load_portfolio(source)
    warnings: list[str] = []

    # Merge duplicate tickers (A 30% + A 20% -> A 50%)
    seen: dict[str, Holding] = {}
    for h in holdings:
        if h.ticker in seen:
            seen[h.ticker].weight_pct += h.weight_pct
            warnings.append(f"重复标的 {h.ticker}，权重已合并")
        else:
            seen[h.ticker] = h
    holdings = list(seen.values())

    total = sum(h.weight_pct for h in holdings) + cash_pct
    if total == 0:
        raise ValueError("持仓权重之和为 0，无法归一化。请检查输入数据。")
    if abs(total - 100) > 0.1:
        scale = 100 / total
        for h in holdings:
            h.weight_pct *= scale
        cash_pct *= scale
        warnings.append(f"权重之和={total:.1f}%，已自动归一化到100%")
    return holdings, cash_pct, warnings


def _prices_from_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize prices DataFrame -> [code, datetime, close, volume].

    Handles both daily (column `date`) and intraday (column `datetime`).
    """
    df = df.copy()
    if "code" not in df.columns and "ticker" in df.columns:
        df = df.rename(columns={"ticker": "code"})
    # Normalize datetime column name
    if "date" in df.columns and "datetime" not in df.columns:
        df = df.rename(columns={"date": "datetime"})
    if "datetime" not in df.columns:
        raise ValueError("Price input must include date or datetime column")
    if "close" not in df.columns:
        raise ValueError("Price input must include close column")
    if "volume" not in df.columns:
        df["volume"] = pd.NA
    if "code" not in df.columns:
        raise ValueError("Price input must include code or ticker column")

    df["datetime"] = pd.to_datetime(df["datetime"])
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
    df = df.sort_values(["code", "datetime"]).reset_index(drop=True)

    return df[["code", "datetime", "close", "volume"]]


def load_prices(
    source: str | Path | pd.DataFrame | list[dict[str, Any]] | Mapping[str, Any],
) -> pd.DataFrame:
    """Load prices from CSV, DataFrame, or records."""
    if isinstance(source, (str, Path)):
        df = pd.read_csv(source)
    else:
        df = _coerce_frame(source, "prices")
    return _prices_from_frame(df)


def _fundamentals_from_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize fundamentals DataFrame."""
    df = df.copy()
    if "ticker" not in df.columns and "code" in df.columns:
        df = df.rename(columns={"code": "ticker"})
    if "ticker" not in df.columns:
        raise ValueError("Fundamentals input must include ticker or code column")

    # Ensure numeric columns
    numeric_cols = [
        "market_cap", "pe_ttm", "pb", "dividend_yield",
        "roe", "debt_to_asset", "earnings_stability",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def load_fundamentals(
    source: str | Path | pd.DataFrame | list[dict[str, Any]] | Mapping[str, Any],
) -> pd.DataFrame:
    """Load fundamentals from CSV, DataFrame, or records."""
    if isinstance(source, (str, Path)):
        df = pd.read_csv(source)
    else:
        df = _coerce_frame(source, "fundamentals")
    return _fundamentals_from_frame(df)


def _benchmark_from_frame(df: pd.DataFrame, code: str) -> pd.DataFrame:
    """Normalize benchmark DataFrame and filter by code when present."""
    df = df.copy()
    if "date" in df.columns and "datetime" not in df.columns:
        df = df.rename(columns={"date": "datetime"})
    if "datetime" not in df.columns:
        raise ValueError("Benchmark input must include date or datetime column")
    if "close" not in df.columns:
        raise ValueError("Benchmark input must include close column")

    if "code" in df.columns:
        filtered = df[df["code"] == code].copy()
    else:
        filtered = df.copy()

    filtered["datetime"] = pd.to_datetime(filtered["datetime"])
    filtered["close"] = pd.to_numeric(filtered["close"], errors="coerce")
    filtered = filtered.sort_values("datetime").reset_index(drop=True)
    return filtered[["datetime", "close"]]


def load_benchmark(
    source: str | Path | pd.DataFrame | list[dict[str, Any]] | Mapping[str, Any],
    code: str,
) -> pd.DataFrame:
    """Load benchmark CSV/DataFrame/records and filter by code if possible.

    Returns DataFrame[datetime, close] for the selected benchmark.
    """
    if isinstance(source, (str, Path)):
        df = pd.read_csv(source)
    else:
        df = _coerce_frame(source, "benchmark")
    return _benchmark_from_frame(df, code)


def truncate_by_lookback(
    df: pd.DataFrame,
    n_bars: int,
    datetime_col: str = "datetime",
) -> pd.DataFrame:
    """Keep only the last n_bars rows per code (or globally if no 'code' column)."""
    if "code" in df.columns:
        parts = [g.tail(n_bars) for _, g in df.groupby("code")]
        return pd.concat(parts, ignore_index=True)
    return df.tail(n_bars).reset_index(drop=True)

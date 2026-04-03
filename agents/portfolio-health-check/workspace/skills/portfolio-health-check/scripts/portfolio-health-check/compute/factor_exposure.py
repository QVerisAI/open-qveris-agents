"""Portfolio-level factor exposure: weighted average of holding factors."""
from __future__ import annotations

FACTOR_ORDER = ["size", "value", "momentum", "quality", "volatility", "liquidity"]


def compute_portfolio_factor_exposure(
    holding_factors: dict[str, dict[str, float]],
    weights: dict[str, float],
    cash_pct: float,
) -> dict:
    """Weighted average of per-holding factors (non-cash, normalized to 1)."""
    codes = [c for c in weights if c in holding_factors]
    missing = [c for c in weights if c not in holding_factors]

    total_w = sum(weights[c] for c in codes)
    if total_w == 0:
        portfolio = {f: 0.0 for f in FACTOR_ORDER}
    else:
        portfolio = {}
        for f in FACTOR_ORDER:
            portfolio[f] = round(
                sum(weights[c] / total_w * holding_factors[c].get(f, 0) for c in codes),
                2,
            )

    return {
        "factor_order": FACTOR_ORDER,
        "holdings": holding_factors,
        "portfolio": portfolio,
        "missing_tickers": missing,
    }

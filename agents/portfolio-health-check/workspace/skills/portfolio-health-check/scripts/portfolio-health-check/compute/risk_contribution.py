"""Risk contribution: MCR, RC, PCT_RC per holding. Sector aggregation."""
from __future__ import annotations

import numpy as np
import pandas as pd

from data_loader import Holding


def compute_risk_contribution(
    holding_returns: dict[str, pd.Series],
    end_weights_raw: dict[str, float],
    holdings: list[Holding],
) -> dict:
    """Compute marginal/risk contribution using end-of-period weights.

    Weights are normalized to sum=1 (non-cash) for the covariance math.
    Output includes both raw and normalized weights.
    """
    codes = list(holding_returns.keys())
    if len(codes) < 2:
        # Single holding: 100% risk contribution
        code = codes[0] if codes else None
        by_holding = []
        if code:
            by_holding.append({
                "code": code,
                "weight_raw": round(end_weights_raw.get(code, 0), 4),
                "weight_normalized": 1.0,
                "marginal_cr": None,
                "risk_contribution": None,
                "pct_risk_contribution": 1.0,
            })
        return {
            "weight_basis": "end_of_period_actual",
            "by_holding": by_holding,
            "by_sector": [],
        }

    # Build returns matrix
    df = pd.DataFrame({c: holding_returns[c] for c in codes}).dropna()
    cov = df.cov()

    # Normalize weights
    total_raw = sum(end_weights_raw[c] for c in codes)
    w = np.array([end_weights_raw[c] / total_raw for c in codes])
    w_raw = np.array([end_weights_raw[c] for c in codes])

    sigma = cov.values
    port_vol = np.sqrt(w @ sigma @ w)

    if port_vol == 0:
        mcr = np.zeros(len(codes))
        rc = np.zeros(len(codes))
        pct_rc = np.zeros(len(codes))
    else:
        mcr = (sigma @ w) / port_vol
        rc = w * mcr
        pct_rc = rc / port_vol

    # Build per-holding results
    code_to_sector = {}
    for h in holdings:
        if not h.is_cash:
            code_to_sector[h.ticker] = h.sector_theme

    by_holding = []
    for i, code in enumerate(codes):
        by_holding.append({
            "code": code,
            "weight_raw": round(float(w_raw[i]), 4),
            "weight_normalized": round(float(w[i]), 4),
            "marginal_cr": round(float(mcr[i]), 4),
            "risk_contribution": round(float(rc[i]), 4),
            "pct_risk_contribution": round(float(pct_rc[i]), 4),
        })

    # Sector aggregation
    sector_rc: dict[str, float] = {}
    for i, code in enumerate(codes):
        sector = code_to_sector.get(code, "unknown")
        sector_rc[sector] = sector_rc.get(sector, 0) + float(pct_rc[i])

    by_sector = [
        {"sector": s, "pct_risk_contribution": round(v, 4)}
        for s, v in sorted(sector_rc.items())
    ]

    return {
        "weight_basis": "end_of_period_actual",
        "by_holding": by_holding,
        "by_sector": by_sector,
    }

"""Tests for compute/risk_contribution.py"""
import pytest
import numpy as np
import pandas as pd
from data_loader import Holding
from compute.risk_contribution import compute_risk_contribution


def _make_data(n=200, seed=42):
    rng = np.random.RandomState(seed)
    returns = {
        "A": pd.Series(rng.normal(0.001, 0.02, n)),
        "B": pd.Series(rng.normal(0.0005, 0.015, n)),
    }
    weights = {"A": 0.6, "B": 0.25}
    holdings = [
        Holding("A", "A", 60, sector_theme="tech"),
        Holding("B", "B", 25, sector_theme="bank"),
    ]
    return returns, weights, holdings


class TestRiskContribution:
    def test_pct_rc_sums_to_one(self):
        returns, weights, holdings = _make_data()
        rc = compute_risk_contribution(returns, weights, holdings)
        total = sum(h["pct_risk_contribution"] for h in rc["by_holding"])
        assert total == pytest.approx(1.0, abs=0.01)

    def test_weight_basis(self):
        returns, weights, holdings = _make_data()
        rc = compute_risk_contribution(returns, weights, holdings)
        assert rc["weight_basis"] == "end_of_period_actual"

    def test_both_raw_and_normalized(self):
        returns, weights, holdings = _make_data()
        rc = compute_risk_contribution(returns, weights, holdings)
        for h in rc["by_holding"]:
            assert "weight_raw" in h
            assert "weight_normalized" in h

    def test_sector_aggregation(self):
        returns, weights, holdings = _make_data()
        rc = compute_risk_contribution(returns, weights, holdings)
        sectors = {s["sector"] for s in rc["by_sector"]}
        assert "tech" in sectors
        assert "bank" in sectors
        sector_total = sum(s["pct_risk_contribution"] for s in rc["by_sector"])
        assert sector_total == pytest.approx(1.0, abs=0.01)

    def test_single_holding(self):
        returns = {"A": pd.Series(np.random.normal(0, 0.02, 50))}
        weights = {"A": 0.85}
        holdings = [Holding("A", "A", 85, sector_theme="tech")]
        rc = compute_risk_contribution(returns, weights, holdings)
        assert rc["by_holding"][0]["pct_risk_contribution"] == 1.0

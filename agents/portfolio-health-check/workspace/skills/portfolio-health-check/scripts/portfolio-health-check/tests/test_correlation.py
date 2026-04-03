"""Tests for compute/correlation.py"""
import pytest
import numpy as np
import pandas as pd

from compute.correlation import compute_correlation_matrix, flag_high_correlations


def _make_returns(n=100, seed=42):
    rng = np.random.RandomState(seed)
    base = rng.normal(0, 0.02, n)
    return {
        "A": pd.Series(base + rng.normal(0, 0.005, n)),
        "B": pd.Series(base + rng.normal(0, 0.005, n)),  # highly correlated with A
        "C": pd.Series(rng.normal(0, 0.02, n)),           # independent
    }


class TestCorrelationMatrix:
    def test_symmetric(self):
        corr = compute_correlation_matrix(_make_returns())
        assert corr.shape == (3, 3)
        pd.testing.assert_frame_equal(corr, corr.T)

    def test_diagonal_is_one(self):
        corr = compute_correlation_matrix(_make_returns())
        for code in corr.columns:
            assert corr.loc[code, code] == pytest.approx(1.0)

    def test_ab_highly_correlated(self):
        corr = compute_correlation_matrix(_make_returns())
        assert corr.loc["A", "B"] > 0.7

    def test_single_holding(self):
        r = {"ONLY": pd.Series(np.random.normal(0, 0.01, 50))}
        corr = compute_correlation_matrix(r)
        assert corr.shape == (1, 1)
        assert corr.iloc[0, 0] == pytest.approx(1.0)


class TestFlagHighCorrelations:
    def test_flags_ab(self):
        corr = compute_correlation_matrix(_make_returns())
        flags = flag_high_correlations(corr, 0.7)
        pairs = [tuple(f["pair"]) for f in flags]
        assert ("A", "B") in pairs

    def test_no_flags_high_threshold(self):
        corr = compute_correlation_matrix(_make_returns())
        flags = flag_high_correlations(corr, 0.99)
        assert len(flags) == 0

    def test_single_holding_no_flags(self):
        r = {"ONLY": pd.Series(np.random.normal(0, 0.01, 50))}
        corr = compute_correlation_matrix(r)
        flags = flag_high_correlations(corr, 0.5)
        assert len(flags) == 0

    def test_correlation_rounded(self):
        corr = compute_correlation_matrix(_make_returns())
        flags = flag_high_correlations(corr, 0.5)
        for f in flags:
            # Should be rounded to 2 decimals
            assert f["correlation"] == round(f["correlation"], 2)

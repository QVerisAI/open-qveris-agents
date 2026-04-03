"""Tests for compute/concentration.py"""
import pytest
from data_loader import Holding
from compute.concentration import (
    compute_hhi, effective_n, top_3_pct, max_holding_pct,
    max_sector_pct, compute_sector_exposure, compute_all_concentration,
)


def _sample_holdings():
    return [
        Holding("A", "A.SH", 30, sector_theme="tech"),
        Holding("B", "B.SZ", 25, sector_theme="energy"),
        Holding("C", "C.SH", 20, sector_theme="tech"),
        Holding("D", "D.SH", 10, sector_theme="bank"),
    ]


class TestHHI:
    def test_equal_weight(self):
        hhi = compute_hhi([0.25, 0.25, 0.25, 0.25])
        assert hhi == pytest.approx(0.25)  # 4 * (0.25)^2

    def test_concentrated(self):
        hhi = compute_hhi([1.0])
        assert hhi == pytest.approx(1.0)

    def test_normalizes(self):
        # Raw weights don't sum to 1
        hhi = compute_hhi([0.30, 0.25, 0.20, 0.10])  # sum=0.85
        # Normalized: 0.353, 0.294, 0.235, 0.118
        expected = (0.30/0.85)**2 + (0.25/0.85)**2 + (0.20/0.85)**2 + (0.10/0.85)**2
        assert hhi == pytest.approx(expected, rel=1e-6)


class TestEffectiveN:
    def test_equal_weight_4(self):
        assert effective_n(0.25) == pytest.approx(4.0)

    def test_single(self):
        assert effective_n(1.0) == pytest.approx(1.0)


class TestTop3:
    def test_basic(self):
        assert top_3_pct([0.30, 0.25, 0.20, 0.10]) == pytest.approx(0.75)


class TestMaxSector:
    def test_basic(self):
        # tech = 30+20 = 50%
        assert max_sector_pct(_sample_holdings()) == pytest.approx(0.50)


class TestSectorExposure:
    def test_excludes_cash(self):
        h = _sample_holdings() + [Holding("cash", "-", 15, vehicle_type="cash", sector_theme="cash")]
        exp = compute_sector_exposure(h)
        assert "cash" not in exp
        assert sum(exp.values()) == pytest.approx(0.85)

    def test_values(self):
        exp = compute_sector_exposure(_sample_holdings())
        assert exp["tech"] == pytest.approx(0.50)
        assert exp["bank"] == pytest.approx(0.10)


class TestComputeAll:
    def test_all_keys(self):
        r = compute_all_concentration(_sample_holdings())
        assert set(r.keys()) == {"hhi", "effective_n", "top_3_pct", "max_holding_pct", "max_sector_pct"}

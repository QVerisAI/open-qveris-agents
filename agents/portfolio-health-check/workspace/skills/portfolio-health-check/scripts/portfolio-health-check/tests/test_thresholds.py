"""Tests for compute/thresholds.py"""
import pytest

from compute.thresholds import (
    get_thresholds,
    apply_horizon_adjustment,
    validate_params,
    RISK_THRESHOLDS,
    HORIZON_MULTIPLIER,
)


# ── get_thresholds ──────────────────────────────────────────────────

class TestGetThresholds:
    def test_all_four_tiers_exist(self):
        for tier in ("conservative", "moderate", "aggressive", "very_aggressive"):
            t = get_thresholds(tier)
            assert isinstance(t, dict)
            assert len(t) == 7

    def test_moderate_values(self):
        t = get_thresholds("moderate")
        assert t["portfolio_vol_high"] == 0.20
        assert t["max_dd_deep"] == 0.15
        assert t["low_sharpe"] == 0.5
        assert t["high_correlation"] == 0.7
        assert t["factor_dominance"] == 0.6
        assert t["sector_cap"] == 0.30
        assert t["holding_cap"] == 0.25

    def test_very_aggressive_has_none(self):
        t = get_thresholds("very_aggressive")
        assert t["portfolio_vol_high"] is None
        assert t["sector_cap"] is None
        assert t["holding_cap"] is None
        # But these are still set
        assert t["max_dd_deep"] == 0.35
        assert t["low_sharpe"] == 0.3

    def test_conservative_stricter_than_moderate(self):
        c = get_thresholds("conservative")
        m = get_thresholds("moderate")
        assert c["portfolio_vol_high"] < m["portfolio_vol_high"]
        assert c["max_dd_deep"] < m["max_dd_deep"]
        assert c["holding_cap"] < m["holding_cap"]

    def test_unknown_tier_raises(self):
        with pytest.raises(ValueError, match="Unknown risk_tolerance"):
            get_thresholds("unknown")

    def test_returns_copy(self):
        t1 = get_thresholds("moderate")
        t1["portfolio_vol_high"] = 999
        t2 = get_thresholds("moderate")
        assert t2["portfolio_vol_high"] == 0.20


# ── apply_horizon_adjustment ────────────────────────────────────────

class TestHorizonAdjustment:
    def test_baseline_no_change(self):
        t = get_thresholds("moderate")
        adj = apply_horizon_adjustment(t, "1-3y")
        assert adj["portfolio_vol_high"] == 0.20
        assert adj["max_dd_deep"] == 0.15

    def test_short_horizon_stricter(self):
        t = get_thresholds("moderate")
        adj = apply_horizon_adjustment(t, "<1y")
        assert adj["portfolio_vol_high"] == pytest.approx(0.20 * 0.8)
        assert adj["max_dd_deep"] == pytest.approx(0.15 * 0.8)

    def test_long_horizon_relaxed(self):
        t = get_thresholds("moderate")
        adj = apply_horizon_adjustment(t, ">5y")
        assert adj["portfolio_vol_high"] == pytest.approx(0.20 * 1.5)
        assert adj["max_dd_deep"] == pytest.approx(0.15 * 1.5)

    def test_only_vol_dd_affected(self):
        t = get_thresholds("moderate")
        adj = apply_horizon_adjustment(t, ">5y")
        assert adj["low_sharpe"] == t["low_sharpe"]
        assert adj["high_correlation"] == t["high_correlation"]
        assert adj["factor_dominance"] == t["factor_dominance"]
        assert adj["sector_cap"] == t["sector_cap"]
        assert adj["holding_cap"] == t["holding_cap"]

    def test_none_threshold_stays_none(self):
        t = get_thresholds("very_aggressive")
        adj = apply_horizon_adjustment(t, "<1y")
        assert adj["portfolio_vol_high"] is None

    def test_unknown_horizon_raises(self):
        t = get_thresholds("moderate")
        with pytest.raises(ValueError, match="Unknown investment_horizon"):
            apply_horizon_adjustment(t, "10y")


# ── validate_params ─────────────────────────────────────────────────

class TestValidateParams:
    def test_valid_moderate_quarterly(self):
        r = validate_params("core_satellite", "quarterly", "daily", "moderate", "3-5y")
        assert r.valid is True
        assert r.error is None

    def test_constant_mix_buy_and_hold_invalid(self):
        r = validate_params("constant_mix", "buy_and_hold", "daily", "moderate", "1-3y")
        assert r.valid is False
        assert "Illegal combination" in r.error

    def test_dca_buy_and_hold_invalid(self):
        r = validate_params("dca", "buy_and_hold", "daily", "moderate", "1-3y")
        assert r.valid is False

    def test_full_rotation_buy_and_hold_degrades(self):
        r = validate_params("full_rotation", "buy_and_hold", "daily", "moderate", "1-3y")
        assert r.valid is True
        assert r.warnings is not None
        assert any("drift" in w for w in r.warnings)

    def test_core_satellite_buy_and_hold_degrades(self):
        r = validate_params("core_satellite", "buy_and_hold", "daily", "moderate", "1-3y")
        assert r.valid is True
        assert r.warnings is not None

    def test_timing_rotation_all_freqs(self):
        for tf in ["intraday", "weekly", "monthly", "quarterly", "buy_and_hold"]:
            r = validate_params("timing_rotation", tf, "daily", "moderate", "1-3y")
            assert r.valid is True, f"timing_rotation + {tf} should be valid"

    def test_15min_only_intraday(self):
        r = validate_params("timing_rotation", "intraday", "15min", "aggressive", "<1y")
        assert r.valid is True

    def test_15min_weekly_invalid(self):
        r = validate_params("timing_rotation", "weekly", "15min", "aggressive", "<1y")
        assert r.valid is False

    def test_weekly_intraday_invalid(self):
        r = validate_params("timing_rotation", "intraday", "weekly", "moderate", "1-3y")
        assert r.valid is False

    def test_unknown_risk_tolerance(self):
        r = validate_params("timing_rotation", "intraday", "daily", "ultra", "1-3y")
        assert r.valid is False

    def test_unknown_horizon(self):
        r = validate_params("timing_rotation", "intraday", "daily", "moderate", "20y")
        assert r.valid is False

    def test_unknown_data_frequency(self):
        r = validate_params("timing_rotation", "intraday", "1min", "moderate", "1-3y")
        assert r.valid is False

    def test_full_rotation_intraday_invalid(self):
        r = validate_params("full_rotation", "intraday", "daily", "moderate", "1-3y")
        assert r.valid is False

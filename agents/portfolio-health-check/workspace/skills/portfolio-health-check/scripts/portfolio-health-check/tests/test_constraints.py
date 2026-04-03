"""Tests for UserConstraints and parse_constraints."""
import pytest

from data_loader import UserConstraints, parse_constraints, CAPITAL_LEVELS


class TestUserConstraintsDefaults:
    def test_default_values(self):
        c = UserConstraints()
        assert c.allowed_markets == ["A-share"]
        assert c.allowed_exposure == ["A-share", "global"]
        assert c.allowed_instruments == ["stock", "etf"]
        assert c.account_permissions == []
        assert c.existing_cash_pct == 0.0
        assert c.additional_capital_ratio == "none"
        assert c.objectives == ["growth"]


class TestParseConstraintsNewFormat:
    def test_new_format_full(self):
        payload = {
            "cash_pct": 15.0,
            "params": {"portfolio_market_value": 1000000},
            "constraints": {
                "allowed_markets": ["A-share", "HK"],
                "allowed_exposure": ["A-share", "HK", "global"],
                "allowed_instruments": ["stock", "etf", "option"],
                "account_permissions": ["option_account"],
                "additional_capital_ratio": "10-30%",
                "objectives": ["growth", "hedge"],
            },
        }
        c = parse_constraints(payload, risk_tolerance="moderate")
        assert c.allowed_markets == ["A-share", "HK"]
        assert "global" in c.allowed_exposure
        assert "option" in c.allowed_instruments
        assert c.account_permissions == ["option_account"]
        assert c.existing_cash_pct == 15.0
        assert c.additional_capital_ratio == "10-30%"
        assert c.objectives == ["growth", "hedge"]
        assert c.portfolio_market_value == 1000000

    def test_new_format_minimal(self):
        payload = {
            "constraints": {"allowed_markets": ["A-share"]},
        }
        c = parse_constraints(payload, risk_tolerance="conservative")
        assert c.allowed_markets == ["A-share"]
        assert "global" in c.allowed_exposure
        assert c.objectives == ["income", "hedge"]  # from conservative default

    def test_new_format_exposure_defaults_to_markets_plus_global(self):
        payload = {
            "constraints": {"allowed_markets": ["A-share", "US"]},
        }
        c = parse_constraints(payload)
        assert "A-share" in c.allowed_exposure
        assert "US" in c.allowed_exposure
        assert "global" in c.allowed_exposure


class TestParseConstraintsOldFormat:
    def test_old_format_migration(self):
        payload = {
            "cash_pct": 10.0,
            "constraints": {
                "investable_markets": ["a_share", "etf", "hk_connect"],
                "available_capital": "10-30%",
                "objectives": ["capital_growth", "hedge_existing_risk"],
            },
        }
        c = parse_constraints(payload, risk_tolerance="moderate")
        assert "A-share" in c.allowed_markets
        assert "HK" in c.allowed_markets
        assert "etf" in c.allowed_instruments
        assert "hk_connect" in c.account_permissions
        assert c.additional_capital_ratio == "10-30%"
        assert c.objectives == ["growth", "hedge"]
        assert c.existing_cash_pct == 10.0
        assert "global" in c.allowed_exposure

    def test_old_format_options(self):
        payload = {
            "constraints": {
                "investable_markets": ["a_share", "options"],
                "available_capital": "none",
                "objectives": ["capital_growth"],
            },
        }
        c = parse_constraints(payload)
        assert "option" in c.allowed_instruments
        assert "option_account" in c.account_permissions
        assert c.additional_capital_ratio == "none"

    def test_old_format_capital_gt50(self):
        payload = {
            "constraints": {
                "investable_markets": ["a_share"],
                "available_capital": ">50%",
                "objectives": [],
            },
        }
        c = parse_constraints(payload, risk_tolerance="aggressive")
        assert c.additional_capital_ratio == "50%+"
        assert c.objectives == ["growth"]  # fallback to risk_tolerance default


class TestParseConstraintsNoConstraints:
    def test_empty_payload(self):
        c = parse_constraints({}, risk_tolerance="moderate")
        assert c.allowed_markets == ["A-share"]
        assert c.objectives == ["growth", "income"]
        assert c.existing_cash_pct == 0.0

    def test_no_constraints_key(self):
        payload = {"cash_pct": 5.0}
        c = parse_constraints(payload, risk_tolerance="conservative")
        assert c.existing_cash_pct == 5.0
        assert c.objectives == ["income", "hedge"]


class TestCapitalLevels:
    def test_all_levels_defined(self):
        assert "none" in CAPITAL_LEVELS
        assert "10-30%" in CAPITAL_LEVELS
        assert "30-50%" in CAPITAL_LEVELS
        assert "50%+" in CAPITAL_LEVELS
        assert CAPITAL_LEVELS["none"] == 0.0
        assert CAPITAL_LEVELS["10-30%"] == 0.20

"""Tests for instrument catalog + strategy templates + filtering."""
import pytest

from data_loader import UserConstraints
from prescribe.strategy_templates import (
    load_catalog, STRATEGY_TEMPLATES, determine_access_req, is_investable,
)


@pytest.fixture
def catalog():
    return load_catalog()


class TestCatalogStructure:
    def test_loads_non_empty(self, catalog):
        assert len(catalog) >= 15

    def test_required_fields(self, catalog):
        required = {"code", "name", "category", "sector", "market",
                    "exposure_market", "instrument_type", "requires_market_permission"}
        for item in catalog:
            missing = required - set(item.keys())
            assert not missing, f"{item['code']} missing: {missing}"

    def test_all_etfs(self, catalog):
        for item in catalog:
            assert item["instrument_type"] == "etf"

    def test_exposure_market_values(self, catalog):
        valid = {"A-share", "US", "HK", "global"}
        for item in catalog:
            assert item["exposure_market"] in valid, f"{item['code']}: {item['exposure_market']}"


class TestStrategyTemplates:
    def test_hold_etf_no_permission(self):
        assert STRATEGY_TEMPLATES["hold_etf"]["requires_permission"] is None

    def test_protective_put_needs_option(self):
        assert STRATEGY_TEMPLATES["protective_put"]["requires_permission"] == "option_account"

    def test_collar_needs_option(self):
        assert STRATEGY_TEMPLATES["collar"]["requires_permission"] == "option_account"

    def test_commodity_hedge_needs_futures(self):
        assert STRATEGY_TEMPLATES["commodity_hedge"]["requires_permission"] == "futures_account"


class TestDetermineAccessReq:
    def test_no_permission_needed(self, catalog):
        etf_300 = next(c for c in catalog if c["code"] == "510300.SH")
        missing = determine_access_req(etf_300, "hold_etf", [])
        assert missing == []

    def test_strategy_needs_option(self, catalog):
        etf_300 = next(c for c in catalog if c["code"] == "510300.SH")
        missing = determine_access_req(etf_300, "protective_put", [])
        assert missing == ["option_account"]

    def test_strategy_permission_already_held(self, catalog):
        etf_300 = next(c for c in catalog if c["code"] == "510300.SH")
        missing = determine_access_req(etf_300, "protective_put", ["option_account"])
        assert missing == []

    def test_instrument_needs_qdii(self, catalog):
        nasdaq = next(c for c in catalog if c["code"] == "513100.SH")
        missing = determine_access_req(nasdaq, "hold_etf", [])
        assert missing == ["qdii"]

    def test_both_instrument_and_strategy_permission(self, catalog):
        nasdaq = next(c for c in catalog if c["code"] == "513100.SH")
        missing = determine_access_req(nasdaq, "protective_put", [])
        assert "qdii" in missing
        assert "option_account" in missing


class TestIsInvestable:
    def test_a_share_etf_passes(self, catalog):
        etf = next(c for c in catalog if c["code"] == "512890.SH")
        constraints = UserConstraints()
        ok, missing = is_investable(etf, "hold_etf", constraints)
        assert ok is True
        assert missing == []

    def test_us_exposure_filtered_when_not_allowed(self, catalog):
        nasdaq = next(c for c in catalog if c["code"] == "513100.SH")
        constraints = UserConstraints(allowed_exposure=["A-share"])
        ok, missing = is_investable(nasdaq, "hold_etf", constraints)
        assert ok is False

    def test_us_exposure_passes_when_allowed(self, catalog):
        nasdaq = next(c for c in catalog if c["code"] == "513100.SH")
        constraints = UserConstraints(
            allowed_exposure=["A-share", "US", "global"],
            account_permissions=["qdii"],
        )
        ok, missing = is_investable(nasdaq, "hold_etf", constraints)
        assert ok is True
        assert missing == []

    def test_option_strategy_returns_missing_perm(self, catalog):
        etf = next(c for c in catalog if c["code"] == "510300.SH")
        constraints = UserConstraints()
        ok, missing = is_investable(etf, "protective_put", constraints)
        assert ok is True
        assert missing == ["option_account"]

    def test_instrument_type_filtered(self, catalog):
        etf = next(c for c in catalog if c["code"] == "512890.SH")
        constraints = UserConstraints(allowed_instruments=["stock"])  # no etf
        ok, missing = is_investable(etf, "hold_etf", constraints)
        assert ok is False

    def test_gold_global_exposure_default_allowed(self, catalog):
        gold = next(c for c in catalog if c["code"] == "518880.SH")
        constraints = UserConstraints()  # default: allowed_exposure includes "global"
        ok, missing = is_investable(gold, "hold_etf", constraints)
        assert ok is True

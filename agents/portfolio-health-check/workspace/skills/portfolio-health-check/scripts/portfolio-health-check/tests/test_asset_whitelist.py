"""Tests for asset whitelist config and loader."""
import pytest

from prescribe.asset_alignment import load_whitelist


@pytest.fixture
def whitelist():
    return load_whitelist()


class TestWhitelistStructure:
    def test_three_categories(self, whitelist):
        assert "main_theme" in whitelist
        assert "defensive" in whitelist
        assert "base_allocation" in whitelist

    def test_main_theme_count(self, whitelist):
        themes = whitelist["main_theme"]
        assert len(themes) == 5
        names = {t["theme_name"] for t in themes}
        assert names == {"AI硬件", "医药", "太空", "机器人", "新能源-储能"}

    def test_main_theme_fields(self, whitelist):
        for theme in whitelist["main_theme"]:
            assert "theme_name" in theme
            assert "subthemes" in theme
            assert "keywords" in theme
            assert "primary_etfs" in theme
            assert "proxy_etfs" in theme
            assert "reference_stocks" in theme
            assert "coverage_level" in theme
            assert theme["coverage_level"] in ("high", "medium", "low")
            assert isinstance(theme["keywords"], list)
            assert len(theme["keywords"]) > 0

    def test_main_theme_etfs_have_code_and_name(self, whitelist):
        for theme in whitelist["main_theme"]:
            for etf in theme["primary_etfs"] + theme.get("proxy_etfs", []):
                assert "code" in etf
                assert "name" in etf

    def test_defensive_count(self, whitelist):
        items = whitelist["defensive"]
        assert len(items) == 3
        names = {d["asset_name"] for d in items}
        assert names == {"黄金", "国债", "红利低波"}

    def test_defensive_fields(self, whitelist):
        for d in whitelist["defensive"]:
            assert "asset_name" in d
            assert "primary_etfs" in d
            assert "trigger_conditions" in d
            assert "suggested_range_pct" in d
            assert isinstance(d["trigger_conditions"], list)
            assert len(d["trigger_conditions"]) > 0
            low, high = d["suggested_range_pct"]
            assert 0 < low < high <= 100

    def test_defensive_trigger_conditions_valid(self, whitelist):
        valid_triggers = {
            "hedge_in_objectives", "income_in_objectives",
            "portfolio_vol_high", "max_dd_deep", "low_sharpe",
            "conservative_or_moderate", "equity_heavy_no_buffer",
        }
        for d in whitelist["defensive"]:
            for t in d["trigger_conditions"]:
                assert t in valid_triggers, f"Unknown trigger: {t} in {d['asset_name']}"

    def test_base_allocation_count(self, whitelist):
        items = whitelist["base_allocation"]
        assert len(items) == 2
        roles = {b["role"] for b in items}
        assert roles == {"core_equity", "cash_equivalent"}

    def test_base_allocation_fields(self, whitelist):
        for b in whitelist["base_allocation"]:
            assert "asset_name" in b
            assert "primary_etfs" in b
            assert "role" in b


class TestLoader:
    def test_load_default_path(self):
        wl = load_whitelist()
        assert isinstance(wl, dict)
        assert len(wl["main_theme"]) > 0

    def test_load_explicit_path(self, tmp_path):
        test_wl = {"main_theme": [], "defensive": [], "base_allocation": []}
        p = tmp_path / "test_wl.json"
        p.write_text(json.dumps(test_wl), encoding="utf-8")
        loaded = load_whitelist(p)
        assert loaded == test_wl


import json

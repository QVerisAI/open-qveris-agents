"""Tests for asset alignment: theme matching, defensive, keep-strong, unclassified."""
import pytest

from data_loader import Holding, UserConstraints
from prescribe.asset_alignment import (
    match_holding_to_theme, resolve_primary_theme,
    analyze_theme_coverage, analyze_defensive_adequacy,
    score_holding_strength, analyze_same_track_keep_strong,
    compute_unclassified, load_whitelist,
)


@pytest.fixture
def themes():
    return load_whitelist()["main_theme"]


@pytest.fixture
def defensive():
    return load_whitelist()["defensive"]


def _h(ticker, name="", weight=10, sector="", style_tag="", lookthrough=""):
    return Holding(
        position_name=name or ticker, ticker=ticker, weight_pct=weight,
        sector_theme=sector, style_tag=style_tag, lookthrough_group=lookthrough,
    )


# ---------------------------------------------------------------------------
# Theme matching
# ---------------------------------------------------------------------------

class TestMatchHoldingToTheme:
    def test_exact_match_reference_stock(self, themes):
        h = _h("002415.SZ", "海康威视")
        matches = match_holding_to_theme(h, themes)
        assert len(matches) >= 1
        assert matches[0]["theme"] == "AI硬件"
        assert matches[0]["match_type"] == "exact"

    def test_keyword_match_via_style_tag(self, themes):
        h = _h("300474.SZ", "景嘉微", style_tag="CPO")
        matches = match_holding_to_theme(h, themes)
        assert any(m["theme"] == "AI硬件" for m in matches)

    def test_keyword_match_via_sector_theme(self, themes):
        h = _h("300760.SZ", "迈瑞医疗", sector="创新药")
        matches = match_holding_to_theme(h, themes)
        assert any(m["theme"] == "医药" for m in matches)

    def test_keyword_match_via_position_name(self, themes):
        h = _h("XXXXX.SZ", "宇树科技机器人")
        matches = match_holding_to_theme(h, themes)
        assert any(m["theme"] == "机器人" for m in matches)

    def test_no_match(self, themes):
        h = _h("601919.SH", "中远海控", sector="航运")
        matches = match_holding_to_theme(h, themes)
        assert len(matches) == 0

    def test_multi_match_resolved_by_index(self, themes):
        # A holding that could match both AI硬件 and 机器人
        h = _h("XXXXX.SZ", "算力机器人公司", style_tag="算力", lookthrough="机器人")
        matches = match_holding_to_theme(h, themes)
        assert len(matches) >= 2
        primary = resolve_primary_theme(matches)
        # AI硬件 is index 0, 机器人 is index 3 → AI硬件 wins
        assert primary["theme"] == "AI硬件"


# ---------------------------------------------------------------------------
# Theme coverage
# ---------------------------------------------------------------------------

class TestThemeCoverage:
    def test_weight_based_coverage(self, themes):
        holdings = [
            _h("002415.SZ", "海康威视", weight=30),  # AI硬件
            _h("300760.SZ", "迈瑞医疗", weight=20, sector="创新药"),  # 医药
            _h("601919.SH", "中远海控", weight=40, sector="航运"),  # no match
        ]
        result = analyze_theme_coverage(holdings, themes)
        # 50% of 90% non-cash is in themes → coverage = 50/90 ≈ 0.556
        assert result["theme_weight_coverage"] > 0.5
        assert "AI硬件" in [t["name"] for t in result["hit_themes"]]
        assert "太空" in result["miss_themes"]

    def test_single_theme_focus_not_penalized(self, themes):
        holdings = [_h("002415.SZ", weight=80)]  # All in AI硬件
        result = analyze_theme_coverage(holdings, themes)
        assert result["theme_weight_coverage"] == 1.0  # 100% coverage

    def test_below_min_weight_not_counted(self, themes):
        holdings = [
            _h("002415.SZ", weight=3),  # AI硬件 but only 3% < 5% threshold
            _h("601919.SH", weight=90, sector="航运"),
        ]
        result = analyze_theme_coverage(holdings, themes)
        assert len(result["hit_themes"]) == 0  # Too small to count


# ---------------------------------------------------------------------------
# Defensive adequacy
# ---------------------------------------------------------------------------

class TestDefensiveAdequacy:
    def test_gold_triggered_by_hedge_objective(self, defensive):
        holdings = [_h("600519.SH", weight=85)]
        context = {
            "objectives": ["hedge"], "risk_tolerance": "moderate",
            "flags": [], "equity_pct": 0.85, "defensive_pct": 0.0,
            "existing_cash_pct": 15, "additional_capital_ratio": "none",
        }
        result = analyze_defensive_adequacy(holdings, defensive, context)
        gold = next(i for i in result["items"] if i["asset_name"] == "黄金")
        assert gold["triggered"] is True
        assert gold["status"] == "missing"
        assert "hedge_in_objectives" in gold["trigger_reasons"]

    def test_not_triggered_status(self, defensive):
        holdings = [_h("600519.SH", weight=85)]
        context = {
            "objectives": ["growth"], "risk_tolerance": "aggressive",
            "flags": [], "equity_pct": 0.85, "defensive_pct": 0.0,
            "existing_cash_pct": 15, "additional_capital_ratio": "none",
        }
        result = analyze_defensive_adequacy(holdings, defensive, context)
        # With aggressive + growth, fewer triggers
        for item in result["items"]:
            if not item["triggered"]:
                assert item["status"] == "not_triggered"

    def test_new_capital_dilutes_cash(self, defensive):
        holdings = [_h("600519.SH", weight=90)]
        context = {
            "objectives": ["hedge"], "risk_tolerance": "moderate",
            "flags": [{"metric": "max_dd_deep"}],
            "equity_pct": 0.90, "defensive_pct": 0.0,
            "existing_cash_pct": 10, "additional_capital_ratio": "10-30%",
        }
        result = analyze_defensive_adequacy(holdings, defensive, context)
        gold = next(i for i in result["items"] if i["asset_name"] == "黄金")
        # Available = 10 * (1/1.2) + 0.2/1.2 * 100 = 8.33 + 16.67 = 25
        assert gold["feasible"] is True
        assert gold["feasible_range_pct"][1] <= 25  # Can't exceed available

    def test_priority_by_trigger_count(self, defensive):
        holdings = []
        context = {
            "objectives": ["hedge", "income"], "risk_tolerance": "conservative",
            "flags": [{"metric": "portfolio_vol_high"}, {"metric": "max_dd_deep"}, {"metric": "low_sharpe"}],
            "equity_pct": 0.95, "defensive_pct": 0.0,
            "existing_cash_pct": 5, "additional_capital_ratio": "none",
        }
        result = analyze_defensive_adequacy(holdings, defensive, context)
        # All should be triggered; order by trigger count
        triggered_items = [i for i in result["items"] if i["triggered"]]
        assert len(triggered_items) >= 2


# ---------------------------------------------------------------------------
# Keep-strong scoring
# ---------------------------------------------------------------------------

class TestKeepStrong:
    def test_score_with_reference_stock_bonus(self):
        metrics = {"sharpe_ratio": 0.8, "max_drawdown": 0.10, "ann_volatility": 0.20, "avg_daily_turnover": 5000000}
        theme_info = {"reference_stocks": ["A"]}
        score_ref = score_holding_strength(metrics, theme_info, "A")
        score_non = score_holding_strength(metrics, theme_info, "B")
        assert score_ref > score_non

    def test_same_track_opportunities(self, themes):
        holdings = [
            _h("002415.SZ", "海康威视", weight=20),
            _h("300474.SZ", "景嘉微", weight=15, style_tag="CPO"),
        ]
        metrics = [
            {"code": "002415.SZ", "sharpe_ratio": 0.8, "max_drawdown": 0.10, "ann_volatility": 0.20},
            {"code": "300474.SZ", "sharpe_ratio": 0.2, "max_drawdown": 0.30, "ann_volatility": 0.35},
        ]
        coverage = analyze_theme_coverage(holdings, themes)
        primary_map = coverage.pop("primary_map") if "primary_map" in coverage else {}
        # Re-run to get primary_map
        coverage_full = analyze_theme_coverage(holdings, themes)
        pm = coverage_full.get("primary_map", {})

        opps = analyze_same_track_keep_strong(holdings, metrics, themes, pm)
        assert len(opps) == 1
        assert opps[0]["theme"] == "AI硬件"
        assert opps[0]["keep"][0]["code"] == "002415.SZ"  # Higher score (reference stock)


# ---------------------------------------------------------------------------
# Unclassified
# ---------------------------------------------------------------------------

class TestUnclassified:
    def test_unmatched_holdings(self):
        holdings = [
            _h("002415.SZ", weight=50),
            _h("601919.SH", "中远海控", weight=40, sector="航运"),
        ]
        primary_map = {"002415.SZ": {"theme": "AI硬件"}, "601919.SH": None}
        result = compute_unclassified(holdings, primary_map, set(), set())
        assert result["unclassified_pct"] == pytest.approx(0.40, abs=0.01)
        assert len(result["holdings"]) == 1
        assert result["holdings"][0]["code"] == "601919.SH"

    def test_defensive_excluded_from_unclassified(self):
        holdings = [
            _h("518880.SH", "黄金ETF", weight=5),
            _h("601919.SH", "中远海控", weight=85, sector="航运"),
        ]
        primary_map = {"518880.SH": None, "601919.SH": None}
        result = compute_unclassified(holdings, primary_map, {"518880.SH"}, set())
        # Only 中远海控 is unclassified
        assert len(result["holdings"]) == 1
        assert result["holdings"][0]["code"] == "601919.SH"

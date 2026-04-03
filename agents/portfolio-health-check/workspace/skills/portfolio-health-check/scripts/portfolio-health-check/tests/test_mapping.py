"""Tests for Stage B mapping engine."""
import pytest

from data_loader import UserConstraints
from prescribe.mapping import (
    compute_objective_weights, compute_suggested_pct, assign_display_tier,
    assign_actionability, score_candidate, map_inferences_to_recommendations,
)


class TestObjectiveWeights:
    def test_single_objective(self):
        w = compute_objective_weights(["hedge"])
        assert w["low_vol"] == 2.0
        assert w["low_corr"] == 2.0

    def test_multi_objective_blend(self):
        w = compute_objective_weights(["income", "hedge"])
        assert w["dividend"] == pytest.approx(1.25)
        assert w["low_vol"] == pytest.approx(1.75)

    def test_empty_falls_back_to_growth(self):
        w = compute_objective_weights([])
        assert w["sector_fill"] == 1.5  # growth default


class TestComputeSuggestedPct:
    def test_use_cash_when_enough(self):
        c = UserConstraints(existing_cash_pct=15)
        pct, funding = compute_suggested_pct(0.10, c)
        assert pct == 0.10
        assert funding == "use_cash"

    def test_new_capital_with_dilution(self):
        c = UserConstraints(existing_cash_pct=5, additional_capital_ratio="10-30%")
        pct, funding = compute_suggested_pct(0.20, c)
        assert funding == "new_capital"
        # diluted_cash = 5/100 * (1/1.2) = 0.0417
        # new_space = 0.2/1.2 = 0.1667
        # total = 0.2083
        assert pct <= 0.21

    def test_skip_when_no_funds(self):
        c = UserConstraints(existing_cash_pct=0, additional_capital_ratio="none")
        pct, funding = compute_suggested_pct(0.10, c)
        assert funding == "skip"
        assert pct == 0.0

    def test_degrade_to_cash_when_small(self):
        c = UserConstraints(existing_cash_pct=3, additional_capital_ratio="none")
        pct, funding = compute_suggested_pct(0.10, c)
        assert funding == "use_cash"
        assert pct == pytest.approx(0.03)


class TestTierAssignment:
    def test_tier1_no_money_no_perm(self):
        assert assign_display_tier("none", []) == 1

    def test_tier1_use_cash(self):
        assert assign_display_tier("use_cash", []) == 1

    def test_tier2_new_capital(self):
        assert assign_display_tier("new_capital", []) == 2

    def test_tier3_any_perm(self):
        assert assign_display_tier("none", ["option_account"]) == 3
        assert assign_display_tier("new_capital", ["qdii"]) == 3
        assert assign_display_tier("use_cash", ["option_account", "qdii"]) == 3


class TestActionability:
    def test_rebalance_is_specific(self):
        assert assign_actionability("rebalance", False) == "specific"

    def test_derivative_is_directional(self):
        assert assign_actionability("hedge", True) == "directional"

    def test_observe_is_directional(self):
        assert assign_actionability("observe", False) == "directional"


class TestScoreCandidate:
    def test_dividend_etf_scores_high_for_income(self):
        w = compute_objective_weights(["income"])
        c = {"category": ["dividend", "low_volatility"], "sector": "broad"}
        s = score_candidate(c, w, set())
        assert s > 3.0

    def test_gold_scores_high_for_hedge(self):
        w = compute_objective_weights(["hedge"])
        c = {"category": ["gold", "hedge"], "sector": "commodity"}
        s = score_candidate(c, w, set())
        assert s > 2.0

    def test_sector_fill_bonus(self):
        w = compute_objective_weights(["growth"])
        c = {"category": [], "sector": "healthcare"}
        s_fill = score_candidate(c, w, {"healthcare"})
        s_no = score_candidate(c, w, set())
        assert s_fill > s_no


class TestMapInferences:
    def test_vol_high_generates_rebalance(self):
        inf = [{
            "signal": "portfolio_vol_high",
            "severity": "high",
            "gap": 0.08,
            "problem_holdings": [
                {"code": "300750.SZ", "name": "宁德时代", "weight_pct": 25,
                 "vol": 0.42, "risk_contrib_pct": 45, "risk_ratio": 1.6,
                 "suggested_new_weight_pct": 15},
            ],
            "best_internal_targets": [{"code": "000858.SZ", "name": "五粮液", "vol": 0.18}],
            "need_external_addition": True,
        }]
        constraints = UserConstraints(existing_cash_pct=10)
        recs = map_inferences_to_recommendations(inf, None, constraints, {"300750.SZ", "000858.SZ"}, set())
        # Should have at least 1 rebalance + some add_asset
        rebalance = [r for r in recs if r["category"] == "rebalance"]
        assert len(rebalance) >= 1
        assert rebalance[0]["funding_req"] == "none"
        assert rebalance[0]["tier"] == 1
        assert rebalance[0]["implementation_plan"]["style"] in ("phased", "single_step")
        if "full_target_pct" in rebalance[0]["targets"][0]:
            assert rebalance[0]["targets"][0]["to_pct"] >= rebalance[0]["targets"][0]["full_target_pct"]

    def test_no_add_when_skip(self):
        inf = [{
            "signal": "portfolio_vol_high",
            "gap": 0.08,
            "problem_holdings": [],
            "best_internal_targets": [],
            "need_external_addition": True,
        }]
        constraints = UserConstraints(existing_cash_pct=0, additional_capital_ratio="none")
        recs = map_inferences_to_recommendations(inf, None, constraints, set(), set())
        add_recs = [r for r in recs if r["category"] == "add_asset"]
        assert len(add_recs) == 0  # No funds -> skip

    def test_add_asset_uses_small_first_step(self):
        inf = [{
            "signal": "portfolio_vol_high",
            "gap": 0.08,
            "problem_holdings": [],
            "best_internal_targets": [],
            "need_external_addition": True,
        }]
        constraints = UserConstraints(existing_cash_pct=10)
        recs = map_inferences_to_recommendations(inf, None, constraints, {"600519.SH"}, set())
        add_recs = [r for r in recs if r["category"] == "add_asset"]
        assert add_recs
        inst = add_recs[0]["instruments"][0]
        assert inst["suggested_pct"] <= inst["full_target_pct"]
        assert add_recs[0]["implementation_plan"]["style"] in ("phased", "single_step")
        assert "先用小仓位" in add_recs[0]["summary_plain"]

    def test_all_recs_have_required_fields(self):
        inf = [{
            "signal": "holding_cap",
            "severity": "high",
            "overweight_holdings": [
                {"code": "600519.SH", "name": "贵州茅台", "weight_pct": 40,
                 "threshold_pct": 25, "excess_pct": 15},
            ],
            "remaining_effective_n": 3.0,
            "need_new_positions": True,
        }]
        constraints = UserConstraints(existing_cash_pct=15)
        recs = map_inferences_to_recommendations(inf, None, constraints, {"600519.SH"}, set())
        required_keys = {
            "id", "tier", "priority", "composite_score", "source_flags",
            "category", "actionability", "funding_req", "access_req",
            "action", "rationale", "potential_side_effects", "assumptions",
            "targets", "instruments", "derivative_strategy", "rescore",
        }
        for r in recs:
            missing = required_keys - set(r.keys())
            assert not missing, f"Missing keys: {missing}"

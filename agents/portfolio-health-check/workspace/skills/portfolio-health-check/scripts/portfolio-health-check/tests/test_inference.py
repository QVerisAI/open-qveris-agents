"""Tests for Stage A inference engine."""
import pytest

from data_loader import UserConstraints
from prescribe.inference import run_all_inferences, _infer_vol_high, _infer_dd_deep, _infer_low_sharpe, _infer_liquidity_tight, _infer_risk_contribution_imbalance


@pytest.fixture
def mock_holdings():
    return [
        {"code": "600519.SH", "name": "贵州茅台", "weight_pct": 30, "ann_volatility": 0.20,
         "max_drawdown": 0.10, "max_dd_recovery_days": 30, "sharpe_ratio": 0.85,
         "ann_return_arithmetic": 0.15, "sector_theme": "消费"},
        {"code": "300750.SZ", "name": "宁德时代", "weight_pct": 25, "ann_volatility": 0.42,
         "max_drawdown": 0.35, "max_dd_recovery_days": None, "sharpe_ratio": 0.20,
         "ann_return_arithmetic": 0.08, "sector_theme": "新能源"},
        {"code": "601318.SH", "name": "中国平安", "weight_pct": 20, "ann_volatility": 0.25,
         "max_drawdown": 0.18, "max_dd_recovery_days": 60, "sharpe_ratio": -0.10,
         "ann_return_arithmetic": -0.02, "sector_theme": "金融"},
        {"code": "000858.SZ", "name": "五粮液", "weight_pct": 15, "ann_volatility": 0.22,
         "max_drawdown": 0.12, "max_dd_recovery_days": 20, "sharpe_ratio": 0.45,
         "ann_return_arithmetic": 0.10, "sector_theme": "消费"},
    ]


@pytest.fixture
def mock_diagnosis(mock_holdings):
    return {
        "risk_flags": [
            {"severity": "high", "metric": "portfolio_vol_high", "actual_value": 0.28, "threshold": 0.20},
            {"severity": "high", "metric": "max_dd_deep", "actual_value": 0.22, "threshold": 0.15},
            {"severity": "medium", "metric": "low_sharpe", "actual_value": 0.15, "threshold": 0.50},
        ],
        "risk_metrics": {
            "portfolio": {"ann_volatility": 0.28, "max_drawdown": 0.22, "sharpe_ratio": 0.15},
            "holdings": mock_holdings,
        },
        "risk_contribution": {
            "by_holding": [
                {"code": "600519.SH", "weight_normalized": 0.33, "pct_risk_contribution": 0.25},
                {"code": "300750.SZ", "weight_normalized": 0.28, "pct_risk_contribution": 0.45},
                {"code": "601318.SH", "weight_normalized": 0.22, "pct_risk_contribution": 0.20},
                {"code": "000858.SZ", "weight_normalized": 0.17, "pct_risk_contribution": 0.10},
            ],
        },
        "concentration": {"effective_n": 3.5, "max_holding_pct": 0.30, "hhi": 0.25},
        "correlation_matrix": {"high_correlation_pairs": []},
        "factor_exposure": {"portfolio": {}, "holdings": {}},
        "sector_exposure": {"消费": 0.45, "新能源": 0.25, "金融": 0.20},
        "liquidity": {
            "portfolio_max_liquidation_days": 3.0,
            "holdings": [
                {"code": "600519.SH", "liquidation_days": 1.0, "avg_daily_turnover": 5000000},
                {"code": "300750.SZ", "liquidation_days": 2.0, "avg_daily_turnover": 3000000},
                {"code": "601318.SH", "liquidation_days": 3.0, "avg_daily_turnover": 2000000},
                {"code": "000858.SZ", "liquidation_days": 1.5, "avg_daily_turnover": 4000000},
            ],
        },
    }


class TestRunAllInferences:
    def test_returns_inferences_for_active_flags(self, mock_diagnosis):
        constraints = UserConstraints(objectives=["growth"])
        results = run_all_inferences(mock_diagnosis, constraints)
        signals = {r["signal"] for r in results}
        assert "portfolio_vol_high" in signals
        assert "max_dd_deep" in signals
        assert "low_sharpe" in signals

    def test_no_flags_returns_empty(self):
        diag = {
            "risk_flags": [],
            "risk_metrics": {"portfolio": {}, "holdings": []},
            "risk_contribution": {"by_holding": []},
            "concentration": {},
            "correlation_matrix": {"high_correlation_pairs": []},
            "factor_exposure": {"portfolio": {}, "holdings": {}},
            "sector_exposure": {},
            "liquidity": {"portfolio_max_liquidation_days": 1.0, "holdings": []},
        }
        results = run_all_inferences(diag, UserConstraints())
        assert len(results) == 0

    def test_liquidity_not_included_when_ok(self, mock_diagnosis):
        constraints = UserConstraints()
        results = run_all_inferences(mock_diagnosis, constraints)
        signals = {r["signal"] for r in results}
        # All holdings have liquidation_days <= 5, so no liquidity signal
        assert "liquidity_tight" not in signals


class TestInferVolHigh:
    def test_identifies_problem_holdings(self, mock_diagnosis):
        flag = mock_diagnosis["risk_flags"][0]
        holdings = mock_diagnosis["risk_metrics"]["holdings"]
        rc = mock_diagnosis["risk_contribution"]
        result = _infer_vol_high(flag, holdings, rc, UserConstraints())
        assert result["signal"] == "portfolio_vol_high"
        assert result["gap"] > 0
        # 300750 has risk_ratio > 1 (0.45/0.28 = 1.6)
        problem_codes = {p["code"] for p in result["problem_holdings"]}
        assert "300750.SZ" in problem_codes

    def test_hedge_objective_increases_reduction(self, mock_diagnosis):
        flag = mock_diagnosis["risk_flags"][0]
        holdings = mock_diagnosis["risk_metrics"]["holdings"]
        rc = mock_diagnosis["risk_contribution"]

        result_normal = _infer_vol_high(flag, holdings, rc, UserConstraints(objectives=["growth"]))
        result_hedge = _infer_vol_high(flag, holdings, rc, UserConstraints(objectives=["hedge"]))

        # Hedge should suggest more aggressive reduction
        if result_normal["problem_holdings"] and result_hedge["problem_holdings"]:
            normal_w = result_normal["problem_holdings"][0]["suggested_new_weight_pct"]
            hedge_w = result_hedge["problem_holdings"][0]["suggested_new_weight_pct"]
            assert hedge_w <= normal_w


class TestInferDdDeep:
    def test_identifies_drag_holdings(self, mock_diagnosis):
        flag = mock_diagnosis["risk_flags"][1]
        holdings = mock_diagnosis["risk_metrics"]["holdings"]
        rc = mock_diagnosis["risk_contribution"]
        result = _infer_dd_deep(flag, holdings, rc)
        assert result["signal"] == "max_dd_deep"
        drag_codes = {d["code"] for d in result["drag_holdings"]}
        assert "300750.SZ" in drag_codes  # dd=0.35 > portfolio dd=0.22
        assert result["portfolio_still_in_drawdown"] is True  # 300750 not recovered


class TestInferLowSharpe:
    def test_identifies_worst_and_best(self, mock_diagnosis):
        flag = mock_diagnosis["risk_flags"][2]
        holdings = mock_diagnosis["risk_metrics"]["holdings"]
        portfolio = mock_diagnosis["risk_metrics"]["portfolio"]
        result = _infer_low_sharpe(flag, holdings, portfolio, UserConstraints())
        assert result["signal"] == "low_sharpe"
        worst_codes = {w["code"] for w in result["worst_holdings"]}
        assert "601318.SH" in worst_codes  # sharpe = -0.10

    def test_income_objective_forces_low_return_cause(self, mock_diagnosis):
        flag = mock_diagnosis["risk_flags"][2]
        holdings = mock_diagnosis["risk_metrics"]["holdings"]
        portfolio = mock_diagnosis["risk_metrics"]["portfolio"]
        result = _infer_low_sharpe(flag, holdings, portfolio, UserConstraints(objectives=["income"]))
        assert result["root_cause"] == "low_return"


class TestInferLiquidityTight:
    def test_no_issue_when_all_under_5(self):
        liq = {"holdings": [{"code": "A", "liquidation_days": 3.0}]}
        result = _infer_liquidity_tight(liq)
        assert result["has_issue"] is False

    def test_flags_holdings_over_5_days(self):
        liq = {"holdings": [
            {"code": "A", "liquidation_days": 8.0, "avg_daily_turnover": 100000},
            {"code": "B", "liquidation_days": 2.0, "avg_daily_turnover": 500000},
        ]}
        result = _infer_liquidity_tight(liq)
        assert result["has_issue"] is True
        assert len(result["tight_holdings"]) == 1
        assert result["tight_holdings"][0]["code"] == "A"


class TestInferRiskContributionImbalance:
    def test_detects_imbalance(self, mock_diagnosis):
        rc = mock_diagnosis["risk_contribution"]
        holdings = mock_diagnosis["risk_metrics"]["holdings"]
        result = _infer_risk_contribution_imbalance(rc, holdings)
        # 300750: pct_rc=0.45 / weight=0.28 = 1.6 — not > 2.0
        # No imbalance with this data
        assert result["has_issue"] is False

    def test_detects_severe_imbalance(self):
        rc = {"by_holding": [
            {"code": "X", "weight_normalized": 0.10, "pct_risk_contribution": 0.40},
        ]}
        holdings = [{"code": "X", "name": "TestX", "weight_pct": 10}]
        result = _infer_risk_contribution_imbalance(rc, holdings)
        assert result["has_issue"] is True
        assert result["imbalanced_holdings"][0]["ratio"] == 4.0

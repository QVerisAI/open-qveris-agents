"""Tests for client-facing structured text output."""
from __future__ import annotations

from structured_output import (
    build_diagnosis_client_output,
    build_prescription_client_output,
)


def test_build_diagnosis_client_output_contains_sections():
    result = {
        "status": "ok",
        "data": {
            "risk_metrics": {
                "portfolio": {
                    "ann_volatility": 0.18,
                    "max_drawdown": 0.12,
                    "sharpe_ratio": 0.82,
                }
            },
            "concentration": {"max_holding_pct": 0.30},
            "benchmark": {"alpha_annual": 0.03, "beta": 0.95},
            "sector_exposure": {"consumer": 0.40},
            "risk_contribution": {"by_holding": [{"code": "600519.SH", "pct_risk_contribution": 0.42}]},
            "risk_flags": [
                {
                    "severity": "high",
                    "metric": "holding_cap",
                    "metric_cn": "单只持仓占比超限",
                    "actual_value": 0.30,
                    "threshold": 0.25,
                    "explanation": "单一持仓偏高。",
                }
            ],
        },
    }

    client_output = build_diagnosis_client_output(result)

    assert client_output["title"] == "组合诊断摘要"
    assert "总体判断" in [item["heading"] for item in client_output["sections"]]
    assert "主要风险提示" in client_output["markdown"]
    assert "单只持仓占比超限" in client_output["markdown"]
    assert any(table["title"] == "关键指标" for table in client_output["tables"])
    assert any(table["title"] == "风险提示明细" for table in client_output["tables"])


def test_build_prescription_client_output_contains_priority_section():
    result = {
        "recommendations": {
            "status": "actionable",
            "message": "组合存在多项可优化点。",
            "constraint_bottlenecks": ["当前没有可用新资金。"],
            "tier_1": {
                "items": [
                    {
                        "action": "先把A从30%降到25%",
                        "summary_plain": "先给重仓位降温。",
                        "potential_side_effects": ["短期收益弹性会下降。"],
                        "assumptions": ["默认愿意做内部调仓。"],
                        "rescore": {"headline": "波动下降0.8个百分点。"},
                    }
                ]
            },
            "tier_2": {"items": []},
            "tier_3": {"items": []},
        },
        "constraints_applied": {
            "allowed_markets": ["A-share"],
            "allowed_exposure": ["A-share", "global"],
            "allowed_instruments": ["stock", "etf"],
            "account_permissions": [],
            "existing_cash_pct": 15.0,
            "additional_capital_ratio": "none",
            "objectives": ["growth", "income"],
        },
        "summary": {"total": 1, "by_tier": {"tier_1": 1, "tier_2": 0, "tier_3": 0}},
    }

    client_output = build_prescription_client_output(result)

    assert client_output["title"] == "组合优化建议"
    assert "优先动作" in [item["heading"] for item in client_output["sections"]]
    assert "先把A从30%降到25%" in client_output["markdown"]
    assert "副作用提醒" in client_output["markdown"]
    assert any(table["title"] == "约束条件" for table in client_output["tables"])
    assert client_output["recommendation_cards"][0]["metrics_table"]["title"] == "前后对比"
    assert client_output["recommendation_cards"][0]["simulation_table"]["title"] == "两档模拟"
    constraints_table = next(table for table in client_output["tables"] if table["title"] == "约束条件")
    assert ["可投资市场", "A股"] in constraints_table["rows"]
    assert ["工具类型", "个股 / ETF"] in constraints_table["rows"]
    assert ["可追加资金", "无新增资金"] in constraints_table["rows"]
    assert ["优化目标", "资产增值 / 稳定现金流"] in constraints_table["rows"]

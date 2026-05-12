"""Tests for qveris_client.py — narrow scope:

1. _normalize_cn_code: pure static, easy to lock the prefix→suffix mapping.
2. execute_tool payload shape: assert search_id key is only present when
   provided; assert keyword-only enforcement so the old positional contract
   re-introduces loudly via TypeError instead of silently sending search_id
   into the `parameters` slot.

We do not test the broader networked behavior — that's covered manually
end-to-end in PR validation.
"""

import os
import pytest

# Ensure QVerisClient init doesn't raise on missing token in CI.
os.environ.setdefault("QVERIS_TOKEN", "test-token")

from qveris_client import QVerisClient  # noqa: E402


class TestNormalizeCnCode:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("600519", "600519.SH"),  # 沪市 A
            ("900001", "900001.SH"),  # 沪 B
            ("000001", "000001.SZ"),  # 深市主板
            ("200001", "200001.SZ"),  # 深 B
            ("300001", "300001.SZ"),  # 创业板
            ("430001", "430001.BJ"),  # 北交所（老 NEEQ 段）
            ("832001", "832001.BJ"),  # 北交所
            ("870001", "870001.BJ"),  # 北交所
        ],
    )
    def test_known_prefixes(self, raw, expected):
        assert QVerisClient._normalize_cn_code(raw) == expected

    def test_already_suffixed_passthrough(self):
        assert QVerisClient._normalize_cn_code("600519.SH") == "600519.SH"
        assert QVerisClient._normalize_cn_code("000001.SZ") == "000001.SZ"

    def test_non_digit_passthrough(self):
        assert QVerisClient._normalize_cn_code("AAPL") == "AAPL"

    def test_wrong_length_passthrough(self):
        assert QVerisClient._normalize_cn_code("60051") == "60051"
        assert QVerisClient._normalize_cn_code("6005199") == "6005199"

    def test_unrecognized_first_digit_passthrough(self):
        # 5/7 段当前未映射（ETF/沪市可转债等）；落到 passthrough，让上游回错。
        assert QVerisClient._normalize_cn_code("510300") == "510300"
        assert QVerisClient._normalize_cn_code("700001") == "700001"

    def test_strips_whitespace(self):
        assert QVerisClient._normalize_cn_code("  600519  ") == "600519.SH"


class TestExecuteToolPayloadShape:
    @pytest.fixture
    def client(self, monkeypatch):
        c = QVerisClient()
        captured = {}

        def fake_post(url, payload):
            captured["url"] = url
            captured["payload"] = payload
            return {"success": True, "result": {"status_code": 200, "data": {}}}

        monkeypatch.setattr(c, "_post_json", fake_post)
        return c, captured

    def test_payload_omits_search_id_when_not_provided(self, client):
        c, captured = client
        c.execute_tool(
            tool_id="ths_ifind.history_quotation.v1", parameters={"codes": "600519.SH"}
        )
        assert "search_id" not in captured["payload"]

    def test_payload_includes_search_id_when_provided(self, client):
        c, captured = client
        c.execute_tool(
            tool_id="ths_ifind.history_quotation.v1",
            parameters={"codes": "600519.SH"},
            search_id="sid-xyz",
        )
        assert captured["payload"].get("search_id") == "sid-xyz"

    def test_payload_omits_session_id_when_empty(self, client):
        c, captured = client
        c.execute_tool(tool_id="t", parameters={})
        assert "session_id" not in captured["payload"]

    def test_payload_includes_session_id_when_provided(self, client):
        c, captured = client
        c.execute_tool(tool_id="t", parameters={}, session_id="sess-42")
        assert captured["payload"].get("session_id") == "sess-42"

    def test_url_routes_tool_id(self, client):
        c, captured = client
        c.execute_tool(tool_id="some.tool.v1", parameters={})
        assert captured["url"].endswith("?tool_id=some.tool.v1")

    def test_keyword_only_enforcement_prevents_positional_drift(self, client):
        c, _ = client
        # Old signature was (tool_id, search_id, parameters, ...). If a stale
        # external caller passes positional args today, we want TypeError
        # rather than silently sending the search_id string into parameters.
        with pytest.raises(TypeError):
            c.execute_tool("tool.v1", "stale-search-id", {"codes": "X"})


class TestLookupSecurityProfileStripsIdentifier:
    """Identifier 在函数顶部 strip 一次，覆盖下游 tool 的参数路径——
    CLI 粘贴 / 用户输入容易带尾空格。
    """

    def test_identifier_stripped_before_passing_to_candidates(self, monkeypatch):
        c = QVerisClient()
        captured_params: list[dict] = []

        def fake_run_tool(tool_id, parameters, session_id=""):
            captured_params.append({"tool_id": tool_id, "parameters": parameters})
            return {"result": {"status_code": 200, "data": {}}}

        monkeypatch.setattr(c, "_run_tool", fake_run_tool)
        c.lookup_security_profile("  600519  ")

        # hangseng 主候选：StockObject 数组里的代码经过 _normalize_cn_code → "600519.SH"
        hangseng = next(
            p for p in captured_params if p["tool_id"].startswith("hangseng_polysource")
        )
        assert hangseng["parameters"]["StockObject"] == ["600519.SH"]
        assert " " not in hangseng["parameters"]["StockObject"][0]

        # ths_ifind fallback：codes 同样 strip + 补后缀
        ths = next(p for p in captured_params if p["tool_id"].startswith("ths_ifind"))
        assert ths["parameters"]["codes"] == "600519.SH"
        assert " " not in ths["parameters"]["codes"]


class TestExtractProfileFromHangsengResult:
    """hangseng_polysource.basicCorpInfo.retrieve.v2 返回 4 层嵌套 wrapper
    (result.data.data.data.rows[0])，验证解析路径正确且 ticker 用 fallback 补后缀。
    """

    def test_extracts_name_industry_and_overrides_ticker_with_fallback(self):
        c = QVerisClient()
        raw = {
            "result": {
                "status_code": 200,
                "data": {
                    "message": "操作成功",
                    "data": {
                        "datatype": "map",
                        "datasize": 1,
                        "data": {
                            "rows": [
                                {
                                    "stockcode": "600519",
                                    "chiname": "贵州茅台酒股份有限公司",
                                    "stockname": "贵州茅台",
                                    "industrysw": "食品饮料-白酒Ⅱ-白酒Ⅲ",
                                    "industryzjh": "制造业-酒、饮料和精制茶制造业",
                                    "industryzx": "食品饮料-酒类-白酒",
                                }
                            ]
                        },
                    },
                },
            }
        }

        result = c._extract_profile_from_result(raw, fallback_ticker="600519.SH")
        assert result == {
            "ticker": "600519.SH",  # bare "600519" 被 fallback "600519.SH" 覆盖
            "name": "贵州茅台酒股份有限公司",
            "industry": "食品饮料-白酒Ⅱ-白酒Ⅲ",  # industrysw 优先
        }

    def test_falls_back_to_ths_ifind_path_when_hangseng_shape_missing(self):
        c = QVerisClient()
        # ths_ifind 形态：result.data 是 list[dict]
        raw = {
            "result": {
                "status_code": 200,
                "data": [
                    {
                        "ths_thscode_stock": "000001.SZ",
                        "ths_corp_cn_name_stock": "平安银行股份有限公司",
                    }
                ],
            }
        }

        result = c._extract_profile_from_result(raw, fallback_ticker="000001.SZ")
        assert result is not None
        assert result["name"] == "平安银行股份有限公司"
        # industry 在 ths_ifind 路径下上游返空，符合数据源限制
        assert result["industry"] == ""

    def test_returns_none_when_neither_shape_matches(self):
        c = QVerisClient()
        raw = {"result": {"status_code": 200, "data": {}}}
        assert c._extract_profile_from_result(raw, fallback_ticker="X") is None

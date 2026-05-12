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
    """Identifier 在函数顶部 strip 一次，覆盖所有候选 tool 的参数路径。
    `_normalize_cn_code` 内部只在纯数字 6 位分支 strip，不能覆盖 mcp_gildata
    的 {"query": identifier} 这条候选——CLI 粘贴 / 用户输入常带尾空格。
    """

    def test_identifier_stripped_before_passing_to_all_candidates(self, monkeypatch):
        c = QVerisClient()
        captured_params: list[dict] = []

        def fake_run_tool(tool_id, parameters, session_id=""):
            captured_params.append({"tool_id": tool_id, "parameters": parameters})
            # 返回空 result 让 _extract_profile_from_result 返回 None，
            # 这样循环会尝试所有候选，我们能验证每条都拿到 stripped identifier
            return {"result": {"status_code": 200, "data": {}}}

        monkeypatch.setattr(c, "_run_tool", fake_run_tool)
        c.lookup_security_profile("  600519  ")

        # ths_ifind 走 normalize 路径 → "600519.SH"
        ths = next(p for p in captured_params if p["tool_id"].startswith("ths_ifind"))
        assert ths["parameters"]["codes"] == "600519.SH"
        assert " " not in ths["parameters"]["codes"]

        # mcp_gildata 拿 raw identifier，必须已 strip
        gildata = next(
            p for p in captured_params if p["tool_id"].startswith("mcp_gildata")
        )
        assert gildata["parameters"]["query"] == "600519"
        assert " " not in gildata["parameters"]["query"]

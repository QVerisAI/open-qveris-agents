"""
事件语义检索工具
- search_events(keyword, minutes, ...): 批量检索事件摘要
- get_event_detail(keyword, event_id): 获取单个事件详情

底层通过 Qveris 平台调用 deepseekdata 语义事件检索工具，无需本地
持有 deepseekdata API Key，只需设置 QVERIS_TOKEN 环境变量。
"""

import json
from datetime import datetime, timedelta, timezone
import re
from typing import Optional

from qveris_client import QVerisClient

DATE_FMT = "%Y-%m-%d %H:%M:%S"

# Qveris 上 deepseekdata 事件语义检索工具的固定 ID。
# 直接走 execute，不再经 /search 取 search_id —— 实测 Qveris broker 已
# 不再依赖 search_id；老逻辑里那次 discover 反而成了脆弱点：query 措辞
# 略改就会让目标 tool 跌出 top-N，导致 false-negative 报"工具下线"。
QVERIS_TOOL_ID = "deepseekdata.event_analysis.events.list.v1.32c03d20"

# 实测单响应 ~155KB，设 300KB 留 ~2x 余量防 schema 扩展。
_MAX_RESP = 300_000

_client_singleton: Optional[QVerisClient] = None


def _get_client() -> QVerisClient:
    global _client_singleton
    if _client_singleton is None:
        _client_singleton = QVerisClient()
    return _client_singleton


def _request(params: dict) -> dict:
    client = _get_client()
    envelope = client.execute_tool(
        tool_id=QVERIS_TOOL_ID,
        parameters=params,
        max_response_size=_MAX_RESP,
    )
    if not envelope.get("success"):
        raise RuntimeError(f"Qveris execute_tool failed: {envelope}")

    result = envelope.get("result", {})
    if not isinstance(result, dict):
        raise RuntimeError(
            f"Qveris returned unexpected result type: {type(result).__name__}"
        )

    # Broker 层故障：upstream HTTP 非 200 或 result.error 不为空。
    # 要在尝试解包 data 之前拦截，否则错误会被后面的 shape check 吞掉，诊断变模糊。
    upstream_status = result.get("status_code")
    if upstream_status is not None and upstream_status != 200:
        raise RuntimeError(
            f"Qveris broker upstream HTTP {upstream_status}: "
            f"{result.get('message') or result.get('error')}"
        )
    if result.get("error"):
        raise RuntimeError(f"Qveris broker error: {result.get('error')}")

    # 正常路径：result.data 直接是 deepseekdata 原始响应 {code, msg, data: {list, total}}
    raw = result.get("data")
    # 截断路径：原始响应 > max_response_size 时 Qveris 改放 full_content_file_url，
    # 需要额外下载一次拿到完整响应（内容结构与正常路径一致）。
    if raw is None and result.get("full_content_file_url"):
        raw = client.download_full_content(result["full_content_file_url"])
    if not isinstance(raw, dict):
        raise RuntimeError(
            f"Qveris returned unexpected result shape: {json.dumps(result)[:300]}"
        )
    if raw.get("code") != 0:
        raise RuntimeError(f"deepseekdata API error: {raw.get('msg')}")
    return raw["data"]


def _safe_get(d: dict, *keys, default=None):
    """安全地按路径取嵌套字典的值"""
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k)
        if d is None:
            return default
    return d


_BJT = timezone(timedelta(hours=8))


# 去除摘要开头的"研报"/"研报指出"等前缀及紧跟的标点
_REPORT_PREFIX_RE = re.compile(
    r"^\s*(?:研报指出|研报认为|研报显示|研报提到|研报表示|研报)"
    r"[，,：:；;、。\s]*"
)


def _strip_report_prefix(text: str | None) -> str | None:
    """去除摘要文本开头的"研报指出"等冗余前缀。"""
    if not text:
        return text
    return _REPORT_PREFIX_RE.sub("", text, count=1)


def _format_ts(ts) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts / 1000, tz=_BJT).strftime(DATE_FMT)


# ──────────────────────────────────────────────
#  第一段：批量检索事件摘要
# ──────────────────────────────────────────────
_LEVEL_PRIORITY = {"S级": 0, "A级": 1, "B级": 2, "C级": 3}


def search_events(
    keyword: str,
    minutes: int = 60,
    page_size: int = 10,
) -> dict:
    """
    按时间范围检索事件，返回精简摘要列表。
    内部拉取全量数据后按 S级优先 + 时间倒序 排序，返回 top page_size 条。

    Args:
        keyword:   语义检索关键词
        minutes:   时间窗口（分钟），end=当前时间，start=当前时间-minutes
        page_size: 最终返回条数（默认10）

    Returns:
        {"total": int, "events": [摘要字典]}
    """
    now = datetime.now(tz=_BJT)
    start = now - timedelta(minutes=minutes)

    params = {
        "keyword": keyword,
        "eventPublishDateStart": start.strftime(DATE_FMT),
        "eventPublishDateEnd": now.strftime(DATE_FMT),
        "pageNo": 1,
        "pageSize": page_size,
    }

    data = _request(params)
    events = []
    for item in data.get("list", []):
        meta = item.get("analysisMetadata", {})
        core_logic = meta.get("core_logic_output", {})
        ic_report = meta.get("ic_report_v10_output", {})

        events.append(
            {
                "eventId": item.get("eventId"),
                "compliantTitle": item.get("compliantTitle"),
                "eventPublishDate": _format_ts(item.get("eventPublishDate")),
                "signalLevel": _safe_get(core_logic, "signal_hint", "level"),
                "original_summary": _strip_report_prefix(
                    core_logic.get("original_summary")
                ),
                "summary": _strip_report_prefix(ic_report.get("summary")),
            }
        )

    total = data.get("total", 0)

    events.sort(key=lambda e: e.get("eventPublishDate") or "", reverse=True)
    events.sort(key=lambda e: _LEVEL_PRIORITY.get(e.get("signalLevel"), 99))
    sorted_events = events

    return {"total": total, "events": sorted_events[:page_size]}


# ──────────────────────────────────────────────
#  第二段：获取单个事件详情
# ──────────────────────────────────────────────
def get_event_detail(keyword: str, event_id: str) -> dict | None:
    """
    根据 eventId 获取单条事件的详细分析数据。

    Args:
        keyword:  语义检索关键词（接口必填）
        event_id: 事件ID

    Returns:
        详情字典，未找到返回 None
    """
    params = {
        "keyword": keyword,
        "eventId": event_id,
        "pageNo": 1,
        "pageSize": 1,
    }

    data = _request(params)
    items = data.get("list", [])
    if not items:
        return None

    item = items[0]
    meta = item.get("analysisMetadata", {})
    core_logic = meta.get("core_logic_output", {})
    ic_report = meta.get("ic_report_v10_output", {})
    logic_validation = meta.get("logic_validation_output", {})
    logic_library = meta.get("logic_library_output", {})

    targets_summary = []
    for t in item.get("investmentTargetsSummary", []):
        targets_summary.append(
            {
                "relevance": t.get("relevance"),
                "target_code": t.get("target_code"),
                "target_name": t.get("target_name"),
                "research_opinion": t.get("research_opinion"),
            }
        )

    return {
        "compliantTitle": item.get("compliantTitle"),
        "eventPublishDate": _format_ts(item.get("eventPublishDate")),
        "signalLevel": _safe_get(core_logic, "signal_hint", "level"),
        "original_summary": _strip_report_prefix(core_logic.get("original_summary")),
        "summary": _strip_report_prefix(ic_report.get("summary")),
        "investmentTargetsSummary": targets_summary,
        "investmentLogic": item.get("investmentLogic"),
        "overallReasoningChain": item.get("overallReasoningChain"),
        "keyRisks": item.get("keyRisks"),
        "signalCategory": item.get("signalCategory"),
        "formatted_tree": ic_report.get("formatted_tree"),
        "transmission_logic": ic_report.get("transmission_logic"),
        "logic_library_output": logic_library,
        "historical_cases_analysis": logic_validation.get("historical_cases_analysis"),
    }


# ──────────────────────────────────────────────
#  第三段：每日事件统计（S/A 级计数）
# ──────────────────────────────────────────────
def daily_event_summary(keyword: str, minutes: int = 1440) -> dict:
    """
    统计过去 N 分钟（默认 1440 = 24h）内的 S 级和 A 级事件数量。

    采用分页拉取以获得准确计数：先查总数，再按需翻页收集所有事件的
    signalLevel，最终按等级汇总。

    Returns:
        {
            "window_minutes": 1440,
            "total": 整体命中数,
            "S级": S级数量,
            "A级": A级数量,
            "other": 其他等级数量,
            "start": "2026-04-09 12:00:00",
            "end":   "2026-04-10 12:00:00"
        }
    """
    now = datetime.now(tz=_BJT)
    start = now - timedelta(minutes=minutes)

    batch_size = 50
    params = {
        "keyword": keyword,
        "eventPublishDateStart": start.strftime(DATE_FMT),
        "eventPublishDateEnd": now.strftime(DATE_FMT),
        "pageNo": 1,
        "pageSize": batch_size,
    }

    data = _request(params)
    total = data.get("total", 0)

    levels: list[str] = []
    for item in data.get("list", []):
        meta = item.get("analysisMetadata", {})
        core_logic = meta.get("core_logic_output", {})
        lvl = _safe_get(core_logic, "signal_hint", "level") or "未知"
        levels.append(lvl)

    fetched = len(levels)
    page = 2
    while fetched < total:
        params["pageNo"] = page
        data = _request(params)
        for item in data.get("list", []):
            meta = item.get("analysisMetadata", {})
            core_logic = meta.get("core_logic_output", {})
            lvl = _safe_get(core_logic, "signal_hint", "level") or "未知"
            levels.append(lvl)
        fetched = len(levels)
        page += 1
        if not data.get("list"):
            break

    s_count = sum(1 for l in levels if l == "S级")
    a_count = sum(1 for l in levels if l == "A级")

    return {
        "window_minutes": minutes,
        "total": total,
        "S级": s_count,
        "A级": a_count,
        "other": total - s_count - a_count,
        "start": start.strftime(DATE_FMT),
        "end": now.strftime(DATE_FMT),
    }


# ──────────────────────────────────────────────
#  直接运行时的演示
# ──────────────────────────────────────────────
if __name__ == "__main__":
    import json
    import sys

    sys.stdout.reconfigure(encoding="utf-8")

    print("=" * 60)
    print("【第一段】批量检索事件摘要（最近 1440 分钟，keyword=AI）")
    print("=" * 60)
    result = search_events(keyword="AI", minutes=1440, page_size=10)
    print(f"共命中 {result['total']} 条事件，当前返回 {len(result['events'])} 条：\n")
    for i, ev in enumerate(result["events"], 1):
        print(f"  [{i}] eventId: {ev['eventId']}")
        print(f"      标题: {ev['compliantTitle']}")
        print(f"      发布时间: {ev['eventPublishDate']}")
        print(f"      信号等级: {ev['signalLevel']}")
        print(f"      原始摘要: {(ev['original_summary'] or '')[:80]}...")
        print(f"      产业链总结: {(ev['summary'] or '')[:80]}...")
        print()

    print("\n" + "=" * 60)
    print("【第二段】获取单条事件详情（eventId=76679）")
    print("=" * 60)
    detail = get_event_detail(keyword="AI", event_id="76679")
    if detail:
        print(json.dumps(detail, ensure_ascii=False, indent=2))
    else:
        print("未找到该事件")

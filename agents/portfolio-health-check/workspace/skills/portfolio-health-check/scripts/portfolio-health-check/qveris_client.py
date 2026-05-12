"""Minimal QVeris client for search and execute calls.

Use environment variable QVERIS_TOKEN for authentication.
This file is intentionally small and reusable by portfolio-health-check skills.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, Optional
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from date_utils import shift_months, shift_years, format_ymd

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


@dataclass
class QVerisConfig:
    # 统一从环境变量读取连接配置，便于本地、CI 和生产环境切换。
    api_key: str = os.getenv("QVERIS_TOKEN", "")
    base_url: str = os.getenv("QVERIS_BASE_URL", "https://qveris.ai/api/v1")
    timeout: int = int(os.getenv("QVERIS_TIMEOUT", "60"))
    state_file: str = os.getenv(
        "PORTFOLIO_STATE_FILE",
        str(Path(".cursor/skills/portfolio-health-check/state/portfolio_state.json")),
    )


class QVerisClient:
    # 这个版本号用于缓存产物和结果结构的一致性校验。
    ARTIFACT_FORMAT = "lean-v3"

    def __init__(self, config: Optional[QVerisConfig] = None) -> None:
        self.config = config or QVerisConfig()
        if not self.config.api_key:
            raise ValueError("QVERIS_TOKEN is not set")

        self.headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }

    def _post_json(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        # 所有接口请求都统一走这里，集中处理编码、超时和异常转换。
        data = json.dumps(payload).encode("utf-8")
        request = Request(url, data=data, headers=self.headers, method="POST")
        try:
            with urlopen(request, timeout=self.config.timeout) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise RuntimeError(
                f"QVeris HTTP error {exc.code}: {body or exc.reason}"
            ) from exc
        except URLError as exc:
            raise RuntimeError(f"QVeris request failed: {exc.reason}") from exc

        return json.loads(raw)

    def search_tools(
        self, query: str, limit: int = 10, session_id: str = ""
    ) -> Dict[str, Any]:
        # 将自然语言查询交给 QVeris 的搜索接口，返回候选工具列表。
        payload: Dict[str, Any] = {"query": query, "limit": limit}
        if session_id:
            payload["session_id"] = session_id

        url = f"{self.config.base_url}/search"
        return self._post_json(url, payload)

    @staticmethod
    def _normalize_cn_code(identifier: str) -> str:
        # 上游 ths_ifind / cn_financial_pro 的 company_basics 端点对裸 6 位代码
        # 返回字段全空，必须带交易所后缀（.SH/.SZ/.BJ）；这里按沪深 A 股编码
        # 段做最小推断。已带 . 的直接透传；无法判定的也透传，让上游回报错误。
        s = identifier.strip()
        if "." in s or not s.isdigit() or len(s) != 6:
            return s
        first = s[0]
        if first in ("6", "9"):  # 沪市 A/B
            return f"{s}.SH"
        if first in ("0", "2", "3"):  # 深市 A/B/创业板
            return f"{s}.SZ"
        if first in ("4", "8"):  # 北交所
            return f"{s}.BJ"
        return s

    def lookup_security_profile(
        self, identifier: str, limit: int = 5, session_id: str = ""
    ) -> Dict[str, Any]:
        # 不再用 /search 做 discover-gate（语义匹配脆弱，常把可用工具误判为不存在）；
        # 候选工具是硬编码列表，直接逐个 execute，第一个成功的就用。
        del limit  # 显式标记忽略以便静态检查
        normalized = self._normalize_cn_code(identifier)
        profile_data = None
        _CANDIDATE_TOOLS = [
            ("ths_ifind.company_basics.v1", {"codes": normalized}),
            ("mcp_gildata.companybasicinfo.v1", {"query": identifier}),
        ]
        for target_tool_id, params in _CANDIDATE_TOOLS:
            if profile_data is not None:
                break
            try:
                run_result = self._run_tool(
                    tool_id=target_tool_id,
                    parameters=params,
                    session_id=session_id,
                )
                profile_data = self._extract_profile_from_result(run_result)
            except Exception:
                pass  # Try next candidate or fallback

        return {
            "identifier": identifier,
            "profile": profile_data,
            "tools_found": [],
        }

    def _run_tool(
        self,
        tool_id: str,
        parameters: Dict[str, Any],
        session_id: str = "",
    ) -> Dict[str, Any]:
        """Thin wrapper around execute_tool for lookup_security_profile."""
        return self.execute_tool(
            tool_id=tool_id,
            parameters=parameters,
            session_id=session_id,
        )

    def _extract_profile_from_result(
        self, raw: Dict[str, Any]
    ) -> Optional[Dict[str, str]]:
        """Extract ticker/name/industry from tool execution result.

        Handles two formats:
        - gildata: markdown table in result.data.results[].table_markdown
        - ths_ifind: dict rows via _flatten_result_rows
        """
        # Try gildata markdown table first
        try:
            results_list = raw.get("result", {}).get("data", {}).get("results", [])
            for item in results_list:
                md = item.get("table_markdown", "")
                if not md:
                    continue
                lines = [l.strip() for l in md.strip().split("\n") if l.strip()]
                if len(lines) < 3:
                    continue
                # Split keeping empty fields; strip leading/trailing from | borders
                headers = [h.strip() for h in lines[0].split("|")]
                values = [v.strip() for v in lines[2].split("|")]
                # Remove empty strings caused by leading/trailing |
                if headers and not headers[0]:
                    headers = headers[1:]
                if headers and not headers[-1]:
                    headers = headers[:-1]
                if values and not values[0]:
                    values = values[1:]
                if values and not values[-1]:
                    values = values[:-1]
                # Pad values to match headers if trailing fields are empty
                while len(values) < len(headers):
                    values.append("")
                row = dict(zip(headers, values[: len(headers)]))
                ticker = row.get("股票代码", "")
                name = row.get("股票名称", "") or row.get("中文名称", "")
                industry = (
                    row.get("所属申万行业", "")
                    or row.get("所属证监会行业", "")
                    or row.get("所属中信行业", "")
                )
                if ticker or name:
                    return {"ticker": ticker, "name": name, "industry": industry}
        except Exception:
            pass

        # Try ths_ifind dict rows
        try:
            rows = self._flatten_result_rows(raw)
            if rows:
                row = rows[0]
                return {
                    "ticker": (
                        row.get("ths_thscode_stock")
                        or row.get("thscode")
                        or row.get("code")
                        or ""
                    ),
                    "name": (
                        row.get("ths_corp_cn_name_stock") or row.get("name") or ""
                    ),
                    "industry": (
                        row.get("ths_the_ths_industry_stock")
                        or row.get("ths_the_sw_industry_stock")
                        or row.get("industry")
                        or ""
                    ),
                }
        except Exception:
            pass
        return None

    def build_market_data_plan(self, rebalance_frequency: str) -> Dict[str, Any]:
        # 根据调仓频率选择合适的行情粒度和回溯长度。
        plans = {
            "intraday": {
                "primary": {"interval": "15m", "lookback": "3mo"},
                "secondary": {"interval": "1d", "lookback": "1y"},
            },
            "weekly": {
                "primary": {"interval": "1d", "lookback": "1y"},
                "secondary": None,
            },
            "monthly": {
                "primary": {"interval": "1d", "lookback": "2y"},
                "secondary": None,
            },
            "quarterly": {
                "primary": {"interval": "1wk", "lookback": "3y"},
                "secondary": None,
            },
            "buy_and_hold": {
                "primary": {"interval": "1mo", "lookback": "5y"},
                "secondary": None,
            },
        }

        if rebalance_frequency not in plans:
            raise ValueError(f"Unsupported rebalance_frequency: {rebalance_frequency}")

        return {
            "rebalance_frequency": rebalance_frequency,
            "fields": ["close", "volume"],
            "plan": plans[rebalance_frequency],
            "recommended_queries": [
                f"historical price close volume API {plans[rebalance_frequency]['primary']['interval']} {plans[rebalance_frequency]['primary']['lookback']}",
                f"stock price volume data API close {rebalance_frequency}",
            ],
        }

    def search_market_data_tools(
        self, rebalance_frequency: str, session_id: str = "", limit: int = 5
    ) -> Dict[str, Any]:
        # 先生成计划，再逐条执行推荐查询。
        plan = self.build_market_data_plan(rebalance_frequency)
        results = []
        for query in plan["recommended_queries"]:
            try:
                results.append(
                    self.search_tools(query, limit=limit, session_id=session_id)
                )
            except Exception as exc:
                results.append({"query": query, "error": str(exc)})
        return {"plan": plan, "results": results}

    def _parse_date(self, value: Optional[str] = None) -> date:
        # 允许多种常见日期输入格式，减少调用方格式约束。
        if value:
            for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
                try:
                    return datetime.strptime(value, fmt).date()
                except ValueError:
                    continue
            raise ValueError(f"Unsupported date format: {value}")
        return datetime.now().date()

    def _shift_months(self, anchor: date, months: int) -> date:
        return shift_months(anchor, months)

    def _shift_years(self, anchor: date, years: int) -> date:
        return shift_years(anchor, years)

    def _start_of_year_window(self, anchor: date, years: int) -> date:
        year = anchor.year - years + 1
        return date(year, 1, 1)

    def _format_ymd(self, value: date) -> str:
        return format_ymd(value)

    def _build_ths_history_spec(
        self,
        dataset_id: str,
        codes: str,
        startdate: str,
        enddate: str,
        interval: str,
        note: str,
    ) -> Dict[str, Any]:
        # 历史行情接口的统一描述模板，便于复用和缓存比对。
        return {
            "dataset_id": dataset_id,
            "tool_id": "ths_ifind.history_quotation.v1",
            "query": "ths_ifind history quotation daily weekly monthly stock",
            "note": note,
            "parameters": {
                "codes": codes,
                "startdate": startdate,
                "enddate": enddate,
                "interval": interval,
                "indicators": "close,volume",
            },
            "tool_kind": "history",
        }

    def _build_ths_minute_spec(
        self,
        dataset_id: str,
        codes: str,
        starttime: str,
        endtime: str,
        interval: str = "15",
        note: str = "",
    ) -> Dict[str, Any]:
        # 分钟级行情接口的统一描述模板。
        return {
            "dataset_id": dataset_id,
            "tool_id": "ths_ifind.hf_basic_quotation.v1",
            "query": "ths_ifind hf basic quotation 15 minute",
            "note": note,
            "parameters": {
                "codes": codes,
                "starttime": starttime,
                "endtime": endtime,
                "interval": interval,
            },
            "tool_kind": "minute",
        }

    def build_ths_frequency_plan(
        self,
        rebalance_frequency: str,
        codes: str,
        as_of: Optional[str] = None,
    ) -> Dict[str, Any]:
        # 按调仓频率生成对应的数据覆盖矩阵和最终选中的数据集。
        anchor = self._parse_date(as_of)
        intraday_start = self._shift_months(anchor, 3)
        one_year_start = self._start_of_year_window(anchor, 1)
        two_year_start = self._start_of_year_window(anchor, 2)
        three_year_start = self._start_of_year_window(anchor, 3)
        five_year_start = self._start_of_year_window(anchor, 5)

        coverage = {
            "minute_15m_3m": self._build_ths_minute_spec(
                "minute_15m_3m",
                codes,
                f"{self._format_ymd(intraday_start)} 09:30:00",
                f"{self._format_ymd(anchor)} 15:00:00",
                interval="15",
                note="15分钟数据，回溯3个月",
            ),
            "daily_1y": self._build_ths_history_spec(
                "daily_1y",
                codes,
                self._format_ymd(one_year_start),
                self._format_ymd(anchor),
                "D",
                "日线数据，回溯1年",
            ),
            "weekly_1y": self._build_ths_history_spec(
                "weekly_1y",
                codes,
                self._format_ymd(one_year_start),
                self._format_ymd(anchor),
                "W",
                "周线数据，回溯1年",
            ),
            "monthly_1y": self._build_ths_history_spec(
                "monthly_1y",
                codes,
                self._format_ymd(one_year_start),
                self._format_ymd(anchor),
                "M",
                "月线数据，回溯1年",
            ),
            "daily_2y": self._build_ths_history_spec(
                "daily_2y",
                codes,
                self._format_ymd(two_year_start),
                self._format_ymd(anchor),
                "D",
                "日线数据，回溯2年",
            ),
            "weekly_3y": self._build_ths_history_spec(
                "weekly_3y",
                codes,
                self._format_ymd(three_year_start),
                self._format_ymd(anchor),
                "W",
                "周线数据，回溯3年",
            ),
            "monthly_5y": self._build_ths_history_spec(
                "monthly_5y",
                codes,
                self._format_ymd(five_year_start),
                self._format_ymd(anchor),
                "M",
                "月线数据，回溯5年",
            ),
        }

        matrix = {
            "intraday": ["minute_15m_3m", "daily_1y"],
            "weekly": ["daily_1y"],
            "monthly": ["daily_2y"],
            "quarterly": ["weekly_3y"],
            "buy_and_hold": ["monthly_5y"],
        }

        if rebalance_frequency not in matrix:
            raise ValueError(f"Unsupported rebalance_frequency: {rebalance_frequency}")

        selected_ids = matrix[rebalance_frequency]
        selected = [coverage[dataset_id] for dataset_id in selected_ids]

        return {
            "rebalance_frequency": rebalance_frequency,
            "reference_date": self._format_ymd(anchor),
            "matrix": matrix,
            "coverage": coverage,
            "selected_dataset_ids": selected_ids,
            "selected_datasets": selected,
        }

    def build_ths_coverage_plan(
        self, codes: str, as_of: Optional[str] = None
    ) -> Dict[str, Any]:
        # 需要“全覆盖”时，返回所有可用时间跨度，供一次性抓取使用。
        anchor = self._parse_date(as_of)
        intraday_start = self._shift_months(anchor, 3)
        one_year_start = self._start_of_year_window(anchor, 1)
        two_year_start = self._start_of_year_window(anchor, 2)
        three_year_start = self._start_of_year_window(anchor, 3)
        five_year_start = self._start_of_year_window(anchor, 5)
        plan = {
            "reference_date": self._format_ymd(anchor),
            "coverage": [
                self._build_ths_minute_spec(
                    "minute_15m_3m",
                    codes,
                    f"{self._format_ymd(intraday_start)} 09:30:00",
                    f"{self._format_ymd(anchor)} 15:00:00",
                    interval="15",
                    note="15分钟数据，回溯3个月",
                ),
                self._build_ths_history_spec(
                    "daily_1y",
                    codes,
                    self._format_ymd(one_year_start),
                    self._format_ymd(anchor),
                    "D",
                    "日线数据，回溯1年",
                ),
                self._build_ths_history_spec(
                    "weekly_1y",
                    codes,
                    self._format_ymd(one_year_start),
                    self._format_ymd(anchor),
                    "W",
                    "周线数据，回溯1年",
                ),
                self._build_ths_history_spec(
                    "monthly_1y",
                    codes,
                    self._format_ymd(one_year_start),
                    self._format_ymd(anchor),
                    "M",
                    "月线数据，回溯1年",
                ),
                self._build_ths_history_spec(
                    "daily_2y",
                    codes,
                    self._format_ymd(two_year_start),
                    self._format_ymd(anchor),
                    "D",
                    "日线数据，回溯2年",
                ),
                self._build_ths_history_spec(
                    "weekly_3y",
                    codes,
                    self._format_ymd(three_year_start),
                    self._format_ymd(anchor),
                    "W",
                    "周线数据，回溯3年",
                ),
                self._build_ths_history_spec(
                    "monthly_5y",
                    codes,
                    self._format_ymd(five_year_start),
                    self._format_ymd(anchor),
                    "M",
                    "月线数据，回溯5年",
                ),
                self._build_ths_history_spec(
                    "daily_3m",
                    codes,
                    self._format_ymd(intraday_start),
                    self._format_ymd(anchor),
                    "D",
                    "日线数据，回溯3个月",
                ),
                self._build_ths_history_spec(
                    "daily_3y",
                    codes,
                    self._format_ymd(three_year_start),
                    self._format_ymd(anchor),
                    "D",
                    "日线数据，回溯3年",
                ),
                self._build_ths_history_spec(
                    "daily_5y",
                    codes,
                    self._format_ymd(five_year_start),
                    self._format_ymd(anchor),
                    "D",
                    "日线数据，回溯5年",
                ),
            ],
        }
        return plan

    def _normalize_ths_series(self, series: Any) -> list[Dict[str, Any]]:
        # 将不同字段名的原始返回统一成简化结构，方便后续汇总和导出。
        normalized: list[Dict[str, Any]] = []
        for row in series:
            if not isinstance(row, dict):
                continue
            time_value = row.get("time") or row.get("date")
            normalized.append(
                {
                    "code": row.get("thscode")
                    or row.get("security_code")
                    or row.get("code"),
                    "time": time_value,
                    "close": row.get("close") or row.get("收盘价"),
                    "volume": row.get("volume") or row.get("成交量"),
                }
            )
        return normalized

    def _extract_ths_series(
        self, payload: Dict[str, Any]
    ) -> list[list[Dict[str, Any]]]:
        # 兼容不同返回层级：result.data 可能是单条列表，也可能是多段列表。
        container = payload.get("result", payload)
        data = container.get("data", []) if isinstance(container, dict) else []
        if not data:
            full_content = self._download_full_content(payload)
            if isinstance(full_content, list):
                data = full_content
        if not isinstance(data, list):
            return []
        if data and isinstance(data[0], dict):
            return [data]
        if data and isinstance(data[0], list):
            return [series for series in data if isinstance(series, list)]
        return []

    def _ensure_code_string(self, codes: str | Iterable[str]) -> str:
        if isinstance(codes, str):
            return codes
        normalized = [str(code).strip() for code in codes if str(code).strip()]
        return ",".join(dict.fromkeys(normalized))

    def _execute_named_tool(
        self,
        tool_id: str,
        query: str,
        parameters: Dict[str, Any],
        session_id: str = "",
        limit: int = 10,
        max_response_size: int = 102400,
    ) -> Dict[str, Any]:
        # `query` 保留入参用于向后兼容，但不再用作 discover-gate。
        # 直接走 execute；broker 不需要 search_id，且老 discover 语义
        # 匹配脆弱，常把可用 tool 误判为下线。
        del query, limit  # 显式标记忽略以便静态检查
        return self.execute_tool(
            tool_id=tool_id,
            parameters=parameters,
            session_id=session_id,
            max_response_size=max_response_size,
        )

    def _flatten_result_rows(self, payload: Dict[str, Any]) -> list[Dict[str, Any]]:
        container = payload.get("result", payload)
        data = container.get("data", []) if isinstance(container, dict) else []
        if not data:
            full_content = self._download_full_content(payload)
            if isinstance(full_content, list):
                data = full_content

        rows: list[Dict[str, Any]] = []
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    rows.append(item)
                elif isinstance(item, list):
                    rows.extend(row for row in item if isinstance(row, dict))
        return rows

    def fetch_history_quotation(
        self,
        codes: str | Iterable[str],
        startdate: str,
        enddate: str,
        interval: str = "D",
        indicators: str = "close,volume",
        session_id: str = "",
        limit: int = 10,
    ) -> pd.DataFrame:
        """Fetch THS history quotation and return a normalized DataFrame."""
        payload = self._execute_named_tool(
            tool_id="ths_ifind.history_quotation.v1",
            query="ths_ifind history quotation daily weekly monthly stock",
            parameters={
                "codes": self._ensure_code_string(codes),
                "startdate": startdate,
                "enddate": enddate,
                "interval": interval,
                "indicators": indicators,
            },
            session_id=session_id,
            limit=limit,
        )
        series_list = self._extract_ths_series(payload)
        rows: list[Dict[str, Any]] = []
        for series in series_list:
            rows.extend(self._normalize_ths_series(series))

        if not rows:
            return pd.DataFrame(columns=["code", "date", "close", "volume"])

        df = pd.DataFrame(rows).rename(columns={"time": "date"})
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
        return (
            df[["code", "date", "close", "volume"]]
            .dropna(subset=["code", "date", "close"])
            .reset_index(drop=True)
        )

    def fetch_minute_quotation(
        self,
        codes: str | Iterable[str],
        starttime: str,
        endtime: str,
        interval: str = "15",
        session_id: str = "",
        limit: int = 10,
    ) -> pd.DataFrame:
        """Fetch THS minute quotation and return a normalized DataFrame."""
        payload = self._execute_named_tool(
            tool_id="ths_ifind.hf_basic_quotation.v1",
            query="ths_ifind hf basic quotation 15 minute",
            parameters={
                "codes": self._ensure_code_string(codes),
                "starttime": starttime,
                "endtime": endtime,
                "interval": interval,
            },
            session_id=session_id,
            limit=limit,
        )
        series_list = self._extract_ths_series(payload)
        rows: list[Dict[str, Any]] = []
        for series in series_list:
            rows.extend(self._normalize_ths_series(series))

        if not rows:
            return pd.DataFrame(columns=["code", "datetime", "close", "volume"])

        df = pd.DataFrame(rows).rename(columns={"time": "datetime"})
        df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
        return (
            df[["code", "datetime", "close", "volume"]]
            .dropna(subset=["code", "datetime", "close"])
            .reset_index(drop=True)
        )

    def fetch_market_caps(
        self,
        codes: str | Iterable[str],
        session_id: str = "",
        limit: int = 10,
    ) -> pd.DataFrame:
        """Fetch market-cap snapshot for holdings."""
        payload = self._execute_named_tool(
            tool_id="ths_ifind.real_time_quotation.v1",
            query="ths_ifind real time quotation stock market cap",
            parameters={
                "codes": self._ensure_code_string(codes),
                "indicators": "common",
            },
            session_id=session_id,
            limit=limit,
        )
        rows = self._flatten_result_rows(payload)
        normalized = []
        for row in rows:
            ticker = (
                row.get("thscode") or row.get("ths_thscode_stock") or row.get("code")
            )
            normalized.append(
                {
                    "ticker": ticker,
                    "market_cap": row.get("总市值")
                    or row.get("market_cap")
                    or row.get("总市值(元)"),
                    "name": row.get("证券简称") or row.get("简称") or row.get("name"),
                }
            )
        if not normalized:
            return pd.DataFrame(columns=["ticker", "market_cap", "name"])

        df = pd.DataFrame(normalized)
        df["market_cap"] = pd.to_numeric(df["market_cap"], errors="coerce")
        return df.dropna(subset=["ticker"]).reset_index(drop=True)

    def fetch_company_basics(
        self,
        codes: str | Iterable[str],
        session_id: str = "",
        limit: int = 10,
    ) -> pd.DataFrame:
        """Fetch security basics such as display name and industry label."""
        payload = self._execute_named_tool(
            tool_id="ths_ifind.company_basics.v1",
            query="ths_ifind company basics stock profile industry",
            parameters={"codes": self._ensure_code_string(codes)},
            session_id=session_id,
            limit=limit,
        )
        rows = self._flatten_result_rows(payload)
        normalized = []
        for row in rows:
            ticker = (
                row.get("ths_thscode_stock") or row.get("thscode") or row.get("code")
            )
            normalized.append(
                {
                    "ticker": ticker,
                    "name": row.get("ths_corp_cn_name_stock")
                    or row.get("证券简称")
                    or row.get("name"),
                    "sector_theme": (
                        row.get("ths_the_ths_industry_stock")
                        or row.get("ths_the_sw_industry_stock")
                        or row.get("industry")
                        or row.get("所属行业")
                        or ""
                    ),
                }
            )
        if not normalized:
            return pd.DataFrame(columns=["ticker", "name", "sector_theme"])
        return pd.DataFrame(normalized).dropna(subset=["ticker"]).reset_index(drop=True)

    def _download_full_content(self, payload: Dict[str, Any]) -> Optional[Any]:
        # 如果接口只返回了截断结果，则尝试下载 full_content_file_url 指向的完整数据。
        container = payload.get("result", payload)
        if not isinstance(container, dict):
            return None
        full_url = container.get("full_content_file_url")
        if not full_url:
            return None
        try:
            raw = urlopen(full_url, timeout=self.config.timeout).read().decode("utf-8")
            return json.loads(raw)
        except Exception:
            truncated = container.get("truncated_content")
            if isinstance(truncated, str) and truncated:
                try:
                    return json.loads(truncated)
                except Exception:
                    return None
            return None

    def _summarize_series(self, series: list[Dict[str, Any]]) -> Dict[str, Any]:
        # 对单条序列做轻量汇总，便于快速查看区间特征。
        closes = [
            row["close"] for row in series if isinstance(row.get("close"), (int, float))
        ]
        volumes = [
            row["volume"]
            for row in series
            if isinstance(row.get("volume"), (int, float))
        ]
        return {
            "start": series[0]["time"] if series else "",
            "end": series[-1]["time"] if series else "",
            "rows": len(series),
            "first_close": closes[0] if closes else None,
            "last_close": closes[-1] if closes else None,
            "min_close": min(closes) if closes else None,
            "max_close": max(closes) if closes else None,
            "min_volume": min(volumes) if volumes else None,
            "max_volume": max(volumes) if volumes else None,
            "last_volume": volumes[-1] if volumes else None,
        }

    def _save_artifact(self, filename: str, payload: Dict[str, Any]) -> str:
        # 将每次执行得到的标准化结果落盘，供后续缓存复用和 CSV 导出。
        artifact_dir = Path(self.config.state_file).parent / "artifacts"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / filename
        with open(artifact_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return str(artifact_path)

    def _artifact_manifest_path(self) -> Path:
        return Path(self.config.state_file).parent / "artifact_manifest.json"

    def _load_artifact_manifest(self) -> Dict[str, Any]:
        # 用一个小型清单记录上次运行的指纹，便于判断缓存是否过期。
        manifest_path = self._artifact_manifest_path()
        if not manifest_path.exists():
            return {}
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_artifact_manifest(self, manifest: Dict[str, Any]) -> None:
        # 写入当前运行的指纹信息，供下次执行做一致性检查。
        manifest_path = self._artifact_manifest_path()
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

    def _clear_artifact_cache(self) -> None:
        # 当输入参数变化时，清掉旧的 JSON 产物，避免误复用。
        artifact_dir = Path(self.config.state_file).parent / "artifacts"
        if not artifact_dir.exists():
            return
        for item in artifact_dir.glob("*.json"):
            try:
                item.unlink()
            except Exception:
                pass

    def _build_run_fingerprint(
        self,
        codes: str,
        rebalance_frequency: Optional[str],
        as_of: Optional[str],
        collect_all: bool,
        datasets: list[Dict[str, Any]],
    ) -> str:
        # 将本次运行的关键输入序列化为稳定指纹，用于缓存命中判断。
        payload = {
            "format": self.ARTIFACT_FORMAT,
            "codes": codes,
            "rebalance_frequency": rebalance_frequency or "",
            "as_of": as_of or "",
            "collect_all": collect_all,
            "datasets": [
                {
                    "dataset_id": spec.get("dataset_id"),
                    "tool_id": spec.get("tool_id"),
                    "parameters": spec.get("parameters"),
                }
                for spec in datasets
            ],
        }
        return json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )

    def _load_artifact(self, artifact_path: Path) -> Optional[Dict[str, Any]]:
        # 读取已保存的单个数据集产物。
        if not artifact_path.exists():
            return None
        try:
            with open(artifact_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _artifact_matches_spec(
        self, artifact: Dict[str, Any], spec: Dict[str, Any]
    ) -> bool:
        # 只有格式、数据集和参数都一致，才允许直接复用缓存。
        if not isinstance(artifact, dict):
            return False
        artifact_spec = artifact.get("spec", {})
        if not isinstance(artifact_spec, dict):
            return False
        return (
            artifact.get("format") == self.ARTIFACT_FORMAT
            and artifact_spec.get("dataset_id") == spec.get("dataset_id")
            and artifact_spec.get("tool_id") == spec.get("tool_id")
            and artifact_spec.get("parameters") == spec.get("parameters")
        )

    def _artifact_to_result(
        self, artifact: Dict[str, Any], artifact_path: Path
    ) -> Dict[str, Any]:
        # 将缓存产物转换为和实时执行一致的结果结构。
        rows = artifact.get("rows", artifact.get("normalized_series", []))
        summaries = artifact.get("summaries", [])
        if not summaries and isinstance(rows, list):
            summaries = [
                self._summarize_series(series)
                for series in rows
                if isinstance(series, list)
            ]
        return {
            "artifact_path": str(artifact_path),
            "summaries": summaries,
            "series_count": len(rows) if isinstance(rows, list) else 0,
            "cached": True,
        }

    def export_close_volume_csv(
        self,
        output_csv: str,
        artifact_paths: Optional[list[str]] = None,
    ) -> Dict[str, Any]:
        # 把历史/分钟级产物统一导出成 CSV，字段按实际数据动态决定。
        output_path = Path(output_csv)
        if not output_path.is_absolute():
            output_path = Path.cwd() / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)

        resolved_artifacts: list[Path] = []
        if artifact_paths:
            for item in artifact_paths:
                p = Path(item)
                if not p.is_absolute():
                    p = Path.cwd() / p
                if p.exists():
                    resolved_artifacts.append(p)
        else:
            state = self.load_state()
            stage2 = state.get("stage2", {})
            datasets = []
            if isinstance(stage2, dict):
                if isinstance(stage2.get("datasets"), list):
                    datasets = stage2.get("datasets", [])
                elif isinstance(stage2.get("ths_data", {}).get("datasets"), list):
                    datasets = stage2.get("ths_data", {}).get("datasets", [])
            for dataset in datasets:
                artifact = dataset.get("artifact_path")
                if not artifact:
                    continue
                p = Path(artifact)
                if not p.is_absolute():
                    p = Path.cwd() / p
                if p.exists():
                    resolved_artifacts.append(p)

        rows_out: list[Dict[str, Any]] = []
        for artifact_path in resolved_artifacts:
            artifact = self._load_artifact(artifact_path)
            if not artifact:
                continue
            series_list = artifact.get("rows", artifact.get("normalized_series", []))
            if not isinstance(series_list, list):
                continue
            for series in series_list:
                if not isinstance(series, list):
                    continue
                for row in series:
                    if not isinstance(row, dict):
                        continue

                    # 通过文件名或 spec 判断是分钟线还是日线，决定时间字段名。
                    is_hf = (
                        "minute" in artifact_path.name
                        or artifact.get("spec", {}).get("tool_kind") == "minute"
                    )
                    time_key = "datetime" if is_hf else "date"

                    rows_out.append(
                        {
                            "code": row.get("code", ""),
                            time_key: row.get("time", ""),
                            "close": row.get("close", ""),
                            "volume": row.get("volume", ""),
                        }
                    )

        if not rows_out:
            return {
                "output_csv": str(output_path),
                "rows": 0,
                "artifacts_used": [str(p) for p in resolved_artifacts],
                "error": "No data rows found in artifacts",
            }

        # 直接用首行字段名，兼容 date / datetime 两种输出结构。
        fieldnames = list(rows_out[0].keys())

        with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, quoting=csv.QUOTE_ALL)
            writer.writeheader()
            writer.writerows(rows_out)

        return {
            "output_csv": str(output_path),
            "rows": len(rows_out),
            "artifacts_used": [str(p) for p in resolved_artifacts],
        }

    def collect_ths_datasets(
        self,
        codes: str,
        rebalance_frequency: Optional[str] = None,
        as_of: Optional[str] = None,
        collect_all: bool = False,
        session_id: str = "",
        limit: int = 10,
    ) -> Dict[str, Any]:
        # 主流程：按频率挑选数据集，执行搜索和下载，并把结果写入 state 和 artifact。
        if collect_all:
            plan = self.build_ths_coverage_plan(codes, as_of=as_of)
            datasets = plan["coverage"]
        else:
            if not rebalance_frequency:
                raise ValueError(
                    "rebalance_frequency is required unless collect_all is true"
                )
            plan = self.build_ths_frequency_plan(
                rebalance_frequency, codes, as_of=as_of
            )
            datasets = plan["selected_datasets"]

        run_fingerprint = self._build_run_fingerprint(
            codes, rebalance_frequency, as_of, collect_all, datasets
        )
        manifest = self._load_artifact_manifest()
        if manifest.get("fingerprint") != run_fingerprint:
            self._clear_artifact_cache()
        self._save_artifact_manifest(
            {
                "fingerprint": run_fingerprint,
                "updated_at": self._now_iso(),
                "codes": codes,
                "rebalance_frequency": rebalance_frequency
                if rebalance_frequency
                else "",
                "as_of": as_of if as_of else "",
                "collect_all": collect_all,
            }
        )

        del limit  # 不再用 discover-gate，limit 入参保留只是为了签名兼容
        executed: list[Dict[str, Any]] = []

        for spec in datasets:
            artifact_filename = f"{spec['dataset_id']}_{codes.replace(',', '_')}.json"
            artifact_path = (
                Path(self.config.state_file).parent / "artifacts" / artifact_filename
            )
            cached_artifact = self._load_artifact(artifact_path)
            if cached_artifact and self._artifact_matches_spec(cached_artifact, spec):
                cached_result = self._artifact_to_result(cached_artifact, artifact_path)
                cached_result.update(
                    {
                        "dataset_id": spec["dataset_id"],
                        "tool_id": spec["tool_id"],
                        "parameters": spec["parameters"],
                    }
                )
                executed.append(cached_result)
                continue

            # 不再做 discover-gate（语义 query 命中率不稳定），直接 execute；
            # 真正的失败信号由 broker 返回 status_code/error 暴露。单条失败必须
            # 隔离到本 spec 范围内——保留旧逻辑的 partial-success 语义，让后续
            # dataset 仍能跑完。
            try:
                payload = self.execute_tool(
                    tool_id=spec["tool_id"],
                    parameters=spec["parameters"],
                    session_id=session_id,
                )
            except Exception as exc:  # noqa: BLE001 — 隔离单条 dataset 失败
                executed.append(
                    {
                        "dataset_id": spec["dataset_id"],
                        "tool_id": spec["tool_id"],
                        "parameters": spec["parameters"],
                        "error": repr(exc),
                    }
                )
                continue

            full_content = self._download_full_content(payload)
            if full_content is not None and isinstance(payload.get("result"), dict):
                payload["result"]["resolved_full_content"] = full_content
                if isinstance(full_content, list):
                    payload["result"]["data"] = full_content

            series_list = self._extract_ths_series(payload)
            normalized_series = [
                self._normalize_ths_series(series) for series in series_list
            ]
            summaries = [self._summarize_series(series) for series in normalized_series]
            artifact_path = self._save_artifact(
                artifact_filename,
                {
                    "format": self.ARTIFACT_FORMAT,
                    "spec": spec,
                    "tool_id": spec["tool_id"],
                    "parameters": spec["parameters"],
                    "rows": normalized_series,
                    "summaries": summaries,
                },
            )

            executed.append(
                {
                    "dataset_id": spec["dataset_id"],
                    "tool_id": spec["tool_id"],
                    "parameters": spec["parameters"],
                    "artifact_path": artifact_path,
                    "summaries": summaries,
                    "series_count": len(normalized_series),
                }
            )

        state = self.load_state()
        state["version"] = "1.0"
        state["stage2"] = {
            "parameters": state.get("stage2", {}).get("parameters", {}),
            "codes": codes,
            "collect_all": collect_all,
            "rebalance_frequency": rebalance_frequency if rebalance_frequency else "",
            "reference_date": plan["reference_date"],
            "selected_dataset_ids": plan.get("selected_dataset_ids", []),
            "datasets": executed,
            "saved_at": self._now_iso(),
            "cache": {
                "fingerprint": run_fingerprint,
                "manifest_path": str(self._artifact_manifest_path()),
            },
        }
        state["updated_at"] = self._now_iso()
        self.save_state(state)
        return state["stage2"]

    def load_state(self) -> Dict[str, Any]:
        # 读取本地持久化的阶段性状态；没有文件时返回默认空结构。
        state_path = Path(self.config.state_file)
        if not state_path.exists():
            return {
                "version": "1.0",
                "updated_at": "",
                "stage1": {},
                "stage2": {},
            }
        with open(state_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def save_state(self, state: Dict[str, Any]) -> Dict[str, Any]:
        # 统一写入 state 文件，供后续流程或导出命令复用。
        state_path = Path(self.config.state_file)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        return state

    def update_stage1_state(
        self, identifier: str, lookup_result: Dict[str, Any]
    ) -> Dict[str, Any]:
        # 保存第一阶段的标的识别结果。
        state = self.load_state()
        state["version"] = "1.0"
        state["stage1"] = {
            "identifier": identifier,
            "lookup_result": lookup_result,
            "saved_at": self._now_iso(),
        }
        state["updated_at"] = self._now_iso()
        return self.save_state(state)

    def update_stage2_state(
        self, rebalance_frequency: str, market_result: Dict[str, Any]
    ) -> Dict[str, Any]:
        # 保存第二阶段的市场数据计划/搜索结果。
        state = self.load_state()
        state["version"] = "1.0"
        state["stage2"] = {
            "rebalance_frequency": rebalance_frequency,
            "market_result": market_result,
            "saved_at": self._now_iso(),
        }
        state["updated_at"] = self._now_iso()
        return self.save_state(state)

    def execute_tool(
        self,
        tool_id: str,
        *,
        parameters: Dict[str, Any],
        search_id: str = "",
        session_id: str = "",
        max_response_size: int = 102400,
    ) -> Dict[str, Any]:
        # Qveris broker 现已不再强制 search_id —— 老逻辑里那次 /search 是
        # discover-then-execute 的遗留约束，但 discover 语义匹配很脆弱，
        # 一旦目标 tool 跌出 top-N 就 false-negative，把可用工具误判为不存在。
        # 直接走 execute 端点，broker 自己根据 tool_id 路由即可。
        # 参数顺序之前是 (tool_id, search_id, parameters, ...)，新签名调换
        # 了 search_id 和 parameters 的位置；强制 keyword-only 是为了让
        # 仍按老顺序传位置参数的外部调用方拿到 TypeError 而不是把 search_id
        # 字符串塞进 parameters 后回个畸形 payload。
        payload: Dict[str, Any] = {
            "parameters": parameters,
            "max_response_size": max_response_size,
        }
        if search_id:
            payload["search_id"] = search_id
        if session_id:
            payload["session_id"] = session_id

        url = f"{self.config.base_url}/tools/execute?tool_id={tool_id}"
        return self._post_json(url, payload)


def _print_json(data: Dict[str, Any]) -> None:
    # CLI 输出统一格式化为 pretty JSON，便于人工查看。
    print(json.dumps(data, ensure_ascii=False, indent=2))


def main() -> None:
    # 命令行入口：把不同能力拆成子命令，方便脚本化调用。
    parser = argparse.ArgumentParser(description="QVeris client")
    subparsers = parser.add_subparsers(dest="command", required=True)

    search_parser = subparsers.add_parser("search", help="Search QVeris tools")
    search_parser.add_argument("query", help="Natural language tool query")
    search_parser.add_argument("--limit", type=int, default=10)
    search_parser.add_argument("--session-id", default="")

    lookup_parser = subparsers.add_parser(
        "lookup", aliases=["identify"], help="Lookup stock/company profile tools"
    )
    lookup_parser.add_argument(
        "identifier", nargs="+", help="Stock name(s) or code(s) to identify"
    )
    lookup_parser.add_argument("--limit", type=int, default=5)
    lookup_parser.add_argument("--session-id", default="")

    market_parser = subparsers.add_parser(
        "market-plan", help="Build market data plan by rebalance frequency"
    )
    market_parser.add_argument(
        "rebalance_frequency", help="intraday|weekly|monthly|quarterly|buy_and_hold"
    )

    market_search_parser = subparsers.add_parser(
        "market-search", help="Search market data tools by rebalance frequency"
    )
    market_search_parser.add_argument(
        "rebalance_frequency", help="intraday|weekly|monthly|quarterly|buy_and_hold"
    )
    market_search_parser.add_argument("--limit", type=int, default=5)
    market_search_parser.add_argument("--session-id", default="")

    ths_collect_parser = subparsers.add_parser(
        "ths-collect", help="Collect THS quotation data and persist it"
    )
    ths_collect_parser.add_argument("codes", help="Comma-separated THS codes")
    ths_collect_parser.add_argument(
        "--rebalance-frequency",
        default="",
        help="intraday|weekly|monthly|quarterly|buy_and_hold",
    )
    ths_collect_parser.add_argument(
        "--as-of", dest="as_of", default="", help="Reference date in YYYY-MM-DD"
    )
    ths_collect_parser.add_argument(
        "--all", action="store_true", help="Collect all supported THS timeframes"
    )
    ths_collect_parser.add_argument("--session-id", default="")
    ths_collect_parser.add_argument("--limit", type=int, default=10)

    state_parser = subparsers.add_parser("state", help="Inspect saved structured state")
    state_subparsers = state_parser.add_subparsers(dest="state_command", required=True)
    state_show_parser = state_subparsers.add_parser(
        "show", help="Show current saved state"
    )
    state_path_parser = state_subparsers.add_parser(
        "path", help="Print state file path"
    )

    export_parser = subparsers.add_parser(
        "export-csv", help="Export close/volume rows to CSV"
    )
    export_parser.add_argument("--output", required=True, help="Output CSV path")
    export_parser.add_argument(
        "--artifacts",
        nargs="*",
        default=[],
        help="Optional artifact json paths; if omitted, use stage2.ths_data datasets",
    )

    execute_parser = subparsers.add_parser("execute", help="Execute a QVeris tool")
    execute_parser.add_argument("tool_id", help="Tool id from search results")
    execute_parser.add_argument(
        "parameters", nargs="?", help="JSON string of tool parameters"
    )
    execute_parser.add_argument(
        "--search-id",
        dest="search_id",
        default="",
        help="Optional search id (broker no longer requires it)",
    )
    execute_parser.add_argument(
        "--parameters-json",
        dest="parameters_json",
        help="JSON string of tool parameters",
    )
    execute_parser.add_argument(
        "--parameters-file",
        dest="parameters_file",
        help="Path to a JSON file of tool parameters",
    )
    execute_parser.add_argument("--session-id", default="")
    execute_parser.add_argument("--max-response-size", type=int, default=102400)

    args = parser.parse_args()
    client = QVerisClient()

    if args.command == "search":
        result = client.search_tools(
            args.query, limit=args.limit, session_id=args.session_id
        )
        _print_json(result)
        return

    if args.command in ("lookup", "identify"):
        results = []
        for ident in args.identifier:
            r = client.lookup_security_profile(
                ident, limit=args.limit, session_id=args.session_id
            )
            client.update_stage1_state(ident, r)
            results.append(r)
        _print_json(results if len(results) > 1 else results[0])
        return

    if args.command == "market-plan":
        result = client.build_market_data_plan(args.rebalance_frequency)
        _print_json(result)
        return

    if args.command == "market-search":
        result = client.search_market_data_tools(
            args.rebalance_frequency,
            session_id=args.session_id,
            limit=args.limit,
        )
        client.update_stage2_state(args.rebalance_frequency, result)
        _print_json(result)
        return

    if args.command == "ths-collect":
        result = client.collect_ths_datasets(
            codes=args.codes,
            rebalance_frequency=args.rebalance_frequency or None,
            as_of=args.as_of or None,
            collect_all=args.all,
            session_id=args.session_id,
            limit=args.limit,
        )
        _print_json(result)
        return

    if args.command == "state":
        if args.state_command == "show":
            _print_json(client.load_state())
            return
        if args.state_command == "path":
            print(client.config.state_file)
            return

    if args.command == "export-csv":
        result = client.export_close_volume_csv(
            output_csv=args.output,
            artifact_paths=args.artifacts if args.artifacts else None,
        )
        _print_json(result)
        return

    if args.command == "execute":
        params_source = args.parameters_json or args.parameters
        if args.parameters_file:
            with open(args.parameters_file, "r", encoding="utf-8-sig") as f:
                params_source = f.read()
        if not params_source:
            raise SystemExit(
                "Provide parameters with --parameters-json, --parameters-file, or the positional parameters argument"
            )

        params = json.loads(params_source.lstrip("\ufeff"))
        result = client.execute_tool(
            tool_id=args.tool_id,
            search_id=args.search_id,
            parameters=params,
            session_id=args.session_id,
            max_response_size=args.max_response_size,
        )
        _print_json(result)


if __name__ == "__main__":
    main()

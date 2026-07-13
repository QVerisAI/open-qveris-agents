"""安全内核回归测试：SSRF / 路径遍历 / 符号链接 / 脱敏 / 文件名净化。"""
import os
import sys
from pathlib import Path

import pytest

from _security import (
    UnsafePathError,
    UnsafeUrlError,
    open_safely,
    safe_filename_segment,
    safe_resolve,
    safe_urlopen,
    scrub_error,
    scrub_secret,
    validate_url,
)


# ---------------- SSRF (#3/#4/#5/#6) ----------------

@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",  # AWS/GCP IMDS
        "http://100.100.100.200/",                    # 阿里云 metadata
        "http://[::ffff:127.0.0.1]/",                 # IPv4-mapped IPv6 绕过
        "http://127.0.0.1/",                          # loopback
        "http://10.0.0.5/",                           # 私网
        "http://192.168.1.1/",
        "http://172.16.0.1/",
        "http://100.64.0.1/",                         # RFC6598
        "http://0.0.0.0/",
        "ftp://example.com/",                         # scheme 白名单
        "file:///etc/passwd",
        "http://user:pw@example.com/",                # 内嵌 credentials
        "http://example.com:22/",                     # 端口白名单
    ],
)
def test_validate_url_blocks_ssrf(url):
    with pytest.raises(UnsafeUrlError):
        validate_url(url, allow_dns=False)


@pytest.mark.parametrize(
    "url",
    ["https://qveris.ai/api/v1", "http://93.184.216.34/", "https://8.8.8.8:443/x"],
)
def test_validate_url_allows_public(url):
    assert validate_url(url, allow_dns=False) == url


# ---------------- 路径遍历 / 符号链接 / TOCTOU (#7/#8/#9/#10) ----------------

def test_safe_resolve_blocks_traversal(tmp_path):
    with pytest.raises(UnsafePathError):
        safe_resolve("../../../../etc/passwd", allowed_roots=[tmp_path])


def test_safe_resolve_blocks_absolute_outside_root(tmp_path):
    with pytest.raises(UnsafePathError):
        safe_resolve("/etc/hosts", allowed_roots=[tmp_path])


def test_safe_resolve_blocks_fullwidth_slash_traversal(tmp_path):
    # 全角斜杠 U+FF0F 经 NFKC 归一化成 '/'，再被越界检查拦下
    with pytest.raises(UnsafePathError):
        safe_resolve("..／..／etc／passwd", allowed_roots=[tmp_path])


def test_safe_resolve_allows_file_within_root(tmp_path):
    f = tmp_path / "data.json"
    f.write_text("{}", encoding="utf-8")
    assert safe_resolve(str(f), allowed_roots=[tmp_path]) == f.resolve()


def test_open_safely_reads_within_root(tmp_path):
    f = tmp_path / "ok.txt"
    f.write_text("hello", encoding="utf-8")
    with open_safely(str(f), "r", allowed_roots=[tmp_path]) as fh:
        assert fh.read() == "hello"


def test_open_safely_blocks_symlink_escape(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("top", encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    link = root / "link.txt"
    link.symlink_to(secret)  # root 内的软链指向 root 外
    with pytest.raises(UnsafePathError):
        safe_resolve(str(link), allowed_roots=[root])


# ---------------- 脱敏 / 文件名净化 (#11d/#12) ----------------

def test_scrub_secret_redacts_token_and_key():
    out = scrub_secret("Authorization: Bearer sk-abc123 api_key=deadbeef")
    assert "sk-abc123" not in out
    assert "deadbeef" not in out


def test_scrub_error_hides_home_path():
    home = os.path.expanduser("~")
    out = scrub_error(RuntimeError(f"open failed {home}/secret/x.json"))
    assert home not in out
    assert "~/secret/x.json" in out


def test_safe_filename_segment_neutralizes_traversal():
    seg = safe_filename_segment("a/../../evil,600519")
    assert "/" not in seg          # 无路径分隔符
    assert not seg.startswith(".")  # 不以点开头（防隐藏文件/遍历）
    assert seg not in (".", "..")


def test_safe_filename_segment_pure_dotdot_becomes_safe():
    assert safe_filename_segment("..") == "_"
    out = safe_filename_segment("../../etc")
    assert "/" not in out and not out.startswith(".")


# ---------------- SSRF: 重定向绕过 / DNS 分支 / safe_urlopen ----------------

def test_redirect_handler_revalidates_each_hop():
    # 302 Location 指向 metadata 必须被拒（否则公网服务器可 302 到内网绕过校验）
    from _security.safe_url import _ValidatingRedirectHandler

    h = _ValidatingRedirectHandler(allow_dns=False)
    with pytest.raises(UnsafeUrlError):
        h.redirect_request(None, None, 302, "Found", {}, "http://169.254.169.254/")


def test_safe_urlopen_validates_initial_url():
    with pytest.raises(UnsafeUrlError):
        safe_urlopen("http://127.0.0.1/", timeout=1, allow_dns=False)


def test_validate_url_dns_branch_rejects_localhost():
    # allow_dns=True 覆盖 getaddrinfo 分支：localhost 离线确定解析到 127.0.0.1
    with pytest.raises(UnsafeUrlError):
        validate_url("http://localhost/", allow_dns=True)


# ---------------- 业务层集成：SSRF 下载 / tool_id 编码 ----------------

def test_download_full_content_rejects_metadata_and_falls_back():
    # 上游响应给的 URL 指向 metadata -> 拒绝下载（validate 在联网前抛错），回退 truncated
    from qveris_client import QVerisClient, QVerisConfig

    c = QVerisClient(QVerisConfig(api_key="test-token"))  # 显式 config，不依赖 env 导入时序
    payload = {
        "result": {
            "full_content_file_url": "http://169.254.169.254/latest/meta-data/",
            "truncated_content": '{"ok": true}',
        }
    }
    assert c._download_full_content(payload) == {"ok": True}


def test_execute_tool_percent_encodes_tool_id(monkeypatch):
    from qveris_client import QVerisClient, QVerisConfig

    c = QVerisClient(QVerisConfig(api_key="test-token"))
    seen: dict = {}
    monkeypatch.setattr(c, "_post_json", lambda url, payload: seen.update(url=url) or {})
    c.execute_tool("a&x=1 b/../", parameters={})
    assert "&x=1" not in seen["url"]  # query 注入字符被编码
    assert "%26" in seen["url"] and "%20" in seen["url"]


# ---------------- 业务层集成：报告 XSS ----------------

def _minimal_result(position_name: str, benchmark_name: str, sector_key: str) -> dict:
    pm = {
        "cagr": 0.1, "ann_return_arithmetic": 0.1, "ann_volatility": 0.2,
        "downside_volatility": 0.15, "max_drawdown": 0.1, "max_dd_recovery_days": 30,
        "sharpe_ratio": 0.8, "sortino_ratio": 1.0, "calmar_ratio": 1.0,
        "var_95": -0.03, "cvar_95": -0.04, "worst_period": -0.05,
    }
    hold = {"code": "600519.SH", "name": position_name, "ann_return_arithmetic": 0.1,
            "sharpe_ratio": 0.8, "weight_pct": 100, "ann_volatility": 0.2,
            "max_drawdown": 0.1, "max_dd_recovery_days": 30, "sortino_ratio": 1.0, "var_95": -0.03}
    return {"data": {
        "metadata": {"risk_tolerance": "moderate", "investment_horizon": "3年",
            "position_style": "constant_mix", "data_frequency": "daily", "lookback_period": "1Y",
            "date_range": ["2024-01-01", "2024-12-31"], "data_points": 250,
            "annualization_factor": 252, "computed_at": "2026-07-13T00:00:00", "warnings": []},
        "risk_metrics": {"portfolio": pm, "holdings": [hold]},
        "correlation_matrix": {"labels": ["600519.SH"], "matrix": [[1.0]], "high_correlation_pairs": []},
        "factor_exposure": {"portfolio": {k: 0.0 for k in
            ["size", "value", "momentum", "quality", "volatility", "liquidity"]},
            "factor_order": ["size", "value", "momentum", "quality", "volatility", "liquidity"]},
        "risk_contribution": {"by_holding": [{"code": "600519.SH", "pct_risk_contribution": 1.0, "weight_raw": 1.0}]},
        "concentration": {"hhi": 1.0, "effective_n": 1.0, "top_3_pct": 1.0, "max_holding_pct": 1.0, "max_sector_pct": 1.0},
        "benchmark": {"benchmark_name": benchmark_name, "benchmark_code": "000300.SH",
            "selection_rule": "x", "beta": 1.0, "alpha_annual": 0.01, "tracking_error": 0.05,
            "information_ratio": 0.5, "relative_max_drawdown": -0.02},
        "sector_exposure": {sector_key: 1.0},
        "liquidity": {"holdings": [{"code": "600519.SH", "liquidation_days": 2.0, "avg_daily_turnover": 5e8}],
            "portfolio_market_value": 1e6},
        "risk_flags": [{"severity": "high", "severity_cn": "高", "metric": "vol", "metric_cn": "波动",
            "explanation": "x", "actual_value": "1", "threshold": "0.2"}],
    }}


def test_report_html_escapes_user_and_upstream_fields(tmp_path):
    import generate_report_html

    result = _minimal_result(
        position_name="<script>alert('xss')</script>",
        benchmark_name="<img src=x onerror=alert(1)>",
        sector_key="<b>evil</b>",  # cn_sec 未命中枚举 -> 原样返回，必须被转义
    )
    out = str(tmp_path / "report.html")
    generate_report_html.generate_html(result, out)
    text = Path(out).read_text(encoding="utf-8")
    assert "<script>alert('xss')</script>" not in text
    assert "<img src=x onerror=alert(1)>" not in text
    assert "<b>evil</b>" not in text
    assert "&lt;script&gt;" in text


# ---------------- CLI 入口路径遍历 ----------------

def test_pipeline_main_rejects_traversal(monkeypatch, capsys):
    import pipeline_main

    monkeypatch.setattr(sys, "argv", ["pipeline_main.py", "/etc/hosts"])
    with pytest.raises(SystemExit) as exc:
        pipeline_main.main()
    assert exc.value.code == 2
    assert "路径不安全" in capsys.readouterr().err

"""安全内核回归测试 —— 覆盖 SSRF / 路径遍历 / 符号链接 / 脱敏。

对应安全加固的 12 项问题：validate_url(#3-6)、safe_resolve/open_safely(#7-10)、
scrub_*/safe_filename_segment(#11d/#12)。用 IP 字面量与 tmp_path，不触发真实 DNS/网络。
"""
import os

import pytest

from _security import (
    UnsafePathError,
    UnsafeUrlError,
    open_safely,
    safe_filename_segment,
    safe_resolve,
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

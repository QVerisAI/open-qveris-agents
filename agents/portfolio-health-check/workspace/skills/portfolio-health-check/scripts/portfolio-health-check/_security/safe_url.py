"""URL 安全内核 —— SSRF 防护。

所有对外 urlopen / requests 前必须过 validate_url()。防护面：
scheme/端口白名单、拒绝 URL 内嵌 credentials、DNS 解析后逐 IP 拒绝
私网/loopback/link-local/multicast/云 metadata，IPv4-mapped/compat IPv6
统一按 IPv4 复核（stdlib 不会为映射地址继承 is_private）。
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

__all__ = ["UnsafeUrlError", "validate_url", "safe_urlopen"]


class UnsafeUrlError(ValueError):
    """URL 触发 SSRF 防护规则时抛出。"""


_ALLOWED_SCHEMES = frozenset({"http", "https"})
_ALLOWED_PORTS = frozenset({80, 443})

# stdlib is_private / is_reserved 覆盖不到、但同样危险的网段
_EXTRA_DENY_V4 = (
    ipaddress.ip_network("100.64.0.0/10"),  # RFC6598 运营商级 NAT（阿里云 metadata 100.100.x 系列）
    ipaddress.ip_network("198.18.0.0/15"),  # benchmarking
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("0.0.0.0/8"),
)
_EXTRA_DENY_V6 = (
    ipaddress.ip_network("::ffff:0:0/96"),  # IPv4-mapped 兜底：一律拒绝映射地址
    ipaddress.ip_network("64:ff9b::/96"),   # NAT64
    ipaddress.ip_network("2002::/16"),      # 6to4
    ipaddress.ip_network("2001::/32"),      # Teredo
)
_METADATA_IPS = frozenset(
    {
        "169.254.169.254",  # AWS / GCP / Azure IMDS
        "100.100.100.200",  # 阿里云
        "100.100.100.199",
        "100.100.100.187",
        "fd00:ec2::254",
    }
)


def _reject_ip_obj(ip: ipaddress._BaseAddress) -> None:
    if str(ip) in _METADATA_IPS:
        raise UnsafeUrlError(f"目标是云 metadata 地址: {ip}")
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        raise UnsafeUrlError(f"目标是私网/保留地址: {ip}")
    deny = _EXTRA_DENY_V6 if ip.version == 6 else _EXTRA_DENY_V4
    for net in deny:
        if ip in net:
            raise UnsafeUrlError(f"目标落在禁用网段 {net}: {ip}")


def _reject_ip(ip_str: str) -> None:
    """对单个 IP 字面量执行完整拒绝规则；IPv4-mapped/compat IPv6 递归按 IPv4 复核。"""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError as exc:
        raise UnsafeUrlError(f"无法解析 IP: {ip_str!r}") from exc
    _reject_ip_obj(ip)
    if isinstance(ip, ipaddress.IPv6Address):
        # ::ffff:a.b.c.d（mapped）与 ::a.b.c.d（compat，已废弃）都可能绕过 IPv4 属性判断
        embedded = ip.ipv4_mapped
        if embedded is None and int(ip) >> 32 == 0 and int(ip) & 0xFFFFFFFF:
            embedded = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if embedded is not None:
            _reject_ip_obj(embedded)


def validate_url(url: str, *, allow_dns: bool = True) -> str:
    """校验对外请求 URL，安全则原样返回，否则抛 UnsafeUrlError。

    allow_dns=True 时对主机名做 getaddrinfo 并逐个解析 IP 复核（缓解 DNS rebinding；
    解析与实际连接之间仍存在理论窗口，属已知残余风险）。
    """
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise UnsafeUrlError(f"scheme 不在白名单: {scheme!r}")
    if parts.username or parts.password:
        raise UnsafeUrlError("URL 不得内嵌 credentials")
    host = parts.hostname
    if not host:
        raise UnsafeUrlError("URL 缺少 host")
    port = parts.port if parts.port is not None else (443 if scheme == "https" else 80)
    if port not in _ALLOWED_PORTS:
        raise UnsafeUrlError(f"端口不在白名单: {port}")

    # host 是 IP 字面量 → 直接校验；是域名 → 解析后逐 IP 校验
    try:
        ipaddress.ip_address(host.strip("[]"))
        is_literal = True
    except ValueError:
        is_literal = False

    if is_literal:
        _reject_ip(host.strip("[]"))
        return url

    if allow_dns:
        try:
            infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        except socket.gaierror as exc:
            raise UnsafeUrlError(f"DNS 解析失败: {host}") from exc
        if not infos:
            raise UnsafeUrlError(f"DNS 未返回任何地址: {host}")
        for info in infos:
            _reject_ip(info[4][0])
    return url


class _ValidatingRedirectHandler(HTTPRedirectHandler):
    """跟随 3xx 前对每一跳 Location 重跑 validate_url，堵住 302→内网 的 SSRF 绕过。"""

    def __init__(self, allow_dns: bool = True) -> None:
        self._allow_dns = allow_dns

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl, allow_dns=self._allow_dns)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def safe_urlopen(url, *, timeout, allow_dns: bool = True):
    """校验 URL（含每一跳重定向）后打开。url 可为字符串或已构造的 Request。"""
    target = url.full_url if isinstance(url, Request) else url
    validate_url(target, allow_dns=allow_dns)
    opener = build_opener(_ValidatingRedirectHandler(allow_dns))
    return opener.open(url, timeout=timeout)

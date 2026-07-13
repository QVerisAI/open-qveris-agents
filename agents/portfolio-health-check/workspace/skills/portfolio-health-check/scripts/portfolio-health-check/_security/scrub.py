"""敏感信息脱敏 + 文件名净化。

用于日志、异常消息、落盘内容与用户输入拼进文件名的场景，避免 token /
Authorization / 绝对路径泄露，以及用户输入穿越 artifacts 目录写文件。
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

__all__ = ["scrub_secret", "scrub_error", "safe_filename_segment"]

# key: value / key=value / key: "value" / Bearer value
_SECRET_KV = re.compile(
    r"(?i)(authorization|api[_-]?key|access[_-]?token|token|password|secret)"
    r'(["\']?\s*[:=]\s*["\']?|\s+bearer\s+)'
    r'([^\s"\',;}]+)'
)
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+")
_UNSAFE_FN = re.compile(r"[^A-Za-z0-9._-]")


def scrub_secret(text: str) -> str:
    # 先吃掉 "Bearer <token>"，再吃 key:value —— 否则 "Authorization: Bearer <token>"
    # 会先匹配 key 把 "Bearer" 当值遮蔽，反而漏掉后面的真 token。
    text = _BEARER.sub("Bearer ***", text)
    text = _SECRET_KV.sub(lambda m: f"{m.group(1)}{m.group(2)}***", text)
    return text


def scrub_error(exc: BaseException | str) -> str:
    """把异常/文本里的绝对路径替换为 ~，并遮蔽 secret，供安全回显。"""
    s = exc if isinstance(exc, str) else f"{type(exc).__name__}: {exc}"
    try:
        home = str(Path.home())
        if home:
            s = s.replace(home, "~")
    except (RuntimeError, OSError):
        pass
    return scrub_secret(s)


def safe_filename_segment(seg: str, *, max_len: int = 128) -> str:
    """把任意字符串净化成单段安全文件名：仅保留 [A-Za-z0-9._-]，去掉前导点防 ../ 与隐藏文件。"""
    seg = unicodedata.normalize("NFKC", str(seg))
    seg = _UNSAFE_FN.sub("_", seg)
    seg = seg.lstrip(".")
    return (seg or "_")[:max_len]

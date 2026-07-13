"""portfolio-health-check 安全内核。

所有对外 URL、外部输入路径、落盘/日志脱敏统一收口到本包，避免各调用点
自行实现留下盲区。
"""

from __future__ import annotations

from .path_safety import UnsafePathError, open_safely, safe_resolve
from .safe_url import UnsafeUrlError, safe_urlopen, validate_url
from .scrub import safe_filename_segment, scrub_error, scrub_secret

__all__ = [
    "UnsafePathError",
    "UnsafeUrlError",
    "open_safely",
    "safe_resolve",
    "safe_urlopen",
    "validate_url",
    "safe_filename_segment",
    "scrub_error",
    "scrub_secret",
]

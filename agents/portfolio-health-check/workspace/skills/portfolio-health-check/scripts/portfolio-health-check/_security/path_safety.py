"""路径安全内核 —— 遍历 / 符号链接 / TOCTOU 防护。

所有由外部输入决定的文件路径都必须先过 safe_resolve()，再用返回的
已验证路径读写；需要 open() 的场景用 open_safely()（os.open + O_NOFOLLOW）。
防护面：NFKC 归一化、拒绝控制字符/NUL、root 之下逐层 lstat 拒符号链接、
resolve 后 realpath 必须落在 allowed_roots 内（自然拦截 ../ 与越界绝对路径）。
"""

from __future__ import annotations

import os
import unicodedata
from pathlib import Path
from typing import IO, Iterable

__all__ = ["UnsafePathError", "safe_resolve", "open_safely"]


class UnsafePathError(ValueError):
    """路径触发安全规则时抛出。"""


def _normalize(raw: str | os.PathLike) -> str:
    s = unicodedata.normalize("NFKC", str(raw))  # 全角斜杠 U+FF0F 等归一化成 '/'，交给后续 realpath 拦截
    if "\x00" in s:
        raise UnsafePathError("路径含 NUL 字节")
    if any(ord(c) < 0x20 for c in s):
        raise UnsafePathError("路径含控制字符")
    return s


def _resolved_roots(allowed_roots: Iterable[str | os.PathLike]) -> list[Path]:
    roots = [Path(r).resolve() for r in allowed_roots]
    if not roots:
        raise UnsafePathError("allowed_roots 不能为空")
    return roots


def _reject_symlinks_below_root(candidate_abs: Path, resolved_root: Path) -> None:
    """只检查 root 之下每一层是否符号链接（root 内软链指向 root 外是主要攻击面）。

    root 自身及其祖先可能是系统软链（macOS 的 /var、/etc），不在检查范围。
    candidate 若非文本上落在 root 内（经软链根前缀访问），交给 resolve 后的越界检查兜底。
    """
    try:
        rel = candidate_abs.relative_to(resolved_root)
    except ValueError:
        return
    cur = resolved_root
    for part in rel.parts:
        cur = cur / part
        if cur.is_symlink():  # lstat 语义，不存在则 False
            raise UnsafePathError(f"路径含符号链接: {cur}")


def _within_any(resolved: Path, roots: list[Path]) -> Path | None:
    for root in roots:
        try:
            resolved.relative_to(root)
            return root
        except ValueError:
            continue
    return None


def safe_resolve(
    raw: str | os.PathLike,
    *,
    allowed_roots: Iterable[str | os.PathLike],
    must_exist: bool = True,
) -> Path:
    """归一化 + 校验路径，返回已解析的绝对 Path；越界/遍历/符号链接抛 UnsafePathError。"""
    roots = _resolved_roots(allowed_roots)
    s = _normalize(raw)
    p = Path(s)
    candidate = Path(os.path.abspath(p if p.is_absolute() else Path.cwd() / p))
    resolved = candidate.resolve()
    containing = _within_any(resolved, roots)
    if containing is None:
        raise UnsafePathError(f"路径越出允许根目录: {resolved}")
    _reject_symlinks_below_root(candidate, containing)
    if must_exist and not resolved.exists():
        raise UnsafePathError(f"路径不存在: {resolved}")
    return resolved


def _mode_to_flags(mode: str) -> int:
    if "b" in mode:
        mode = mode.replace("b", "")
    flags = 0
    if "+" in mode:
        flags |= os.O_RDWR
    elif "r" in mode:
        flags |= os.O_RDONLY
    elif "w" in mode:
        flags |= os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    elif "a" in mode:
        flags |= os.O_WRONLY | os.O_CREAT | os.O_APPEND
    elif "x" in mode:
        flags |= os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if "w" in mode and "+" in mode:
        flags |= os.O_CREAT | os.O_TRUNC
    return flags


def open_safely(
    raw: str | os.PathLike,
    mode: str = "r",
    *,
    allowed_roots: Iterable[str | os.PathLike],
    encoding: str | None = "utf-8",
) -> IO:
    """safe_resolve 后用 os.open + O_NOFOLLOW 打开，收窄 resolve 与 open 之间的 TOCTOU 窗口。

    O_NOFOLLOW 仅保护路径最后一段，中间目录组件在 resolve 后被换成软链仍有理论残余风险。
    """
    writing = any(c in mode for c in ("w", "a", "x", "+"))
    resolved = safe_resolve(raw, allowed_roots=allowed_roots, must_exist=not writing)
    flags = _mode_to_flags(mode)
    if hasattr(os, "O_NOFOLLOW"):  # POSIX：symlink 命中 open 触发 ELOOP；Windows 无此常量，仅靠 safe_resolve 的 lstat 前置校验兜底
        flags |= os.O_NOFOLLOW
    fd = os.open(str(resolved), flags, 0o600)
    if "b" in mode:
        return os.fdopen(fd, mode)
    return os.fdopen(fd, mode, encoding=encoding)

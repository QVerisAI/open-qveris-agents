from __future__ import annotations

from pathlib import Path


def get_assets_dir(start: Path | None = None) -> Path:
    """Locate repo-level sample assets from any copied workflow directory."""
    anchor = Path(start) if start is not None else Path(__file__)
    anchor = anchor.resolve()
    search_from = anchor if anchor.is_dir() else anchor.parent

    for parent in (search_from, *search_from.parents):
        candidate = parent / "Interview" / "portfolio-health-check" / "assets"
        if candidate.exists():
            return candidate

    raise FileNotFoundError(
        f"Could not locate sample assets from {search_from}. "
        "Expected Interview/portfolio-health-check/assets in an ancestor directory."
    )


def safe_input_roots(start: Path | None = None) -> list[Path]:
    """外部输入路径的允许根：CWD + 脚本目录 + （若能定位）sample assets 目录。

    assets 位于脚本目录的祖先层（Interview/portfolio-health-check/assets），
    因此必须显式纳入，否则合法数据加载会被 safe_resolve 误判越界。
    """
    roots: list[Path] = [Path.cwd(), Path(__file__).resolve().parent]
    try:
        roots.append(get_assets_dir(start))
    except FileNotFoundError:
        pass
    return roots

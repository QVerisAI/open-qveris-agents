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

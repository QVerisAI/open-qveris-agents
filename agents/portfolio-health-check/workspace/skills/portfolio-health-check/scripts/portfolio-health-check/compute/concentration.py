"""Concentration metrics: HHI, Effective N, Top 3, max holding/sector."""
from __future__ import annotations

from data_loader import Holding


def compute_hhi(weights_non_cash: list[float]) -> float:
    """HHI on normalized non-cash weights (sum=1)."""
    total = sum(weights_non_cash)
    if total == 0:
        return 0.0
    norm = [w / total for w in weights_non_cash]
    return sum(w ** 2 for w in norm)


def effective_n(hhi: float) -> float:
    if hhi == 0:
        return 0.0
    return 1.0 / hhi


def top_3_pct(weights_raw: list[float]) -> float:
    """Sum of top 3 raw weights (fractions, not normalized)."""
    sorted_w = sorted(weights_raw, reverse=True)
    return sum(sorted_w[:3])


def max_holding_pct(weights_raw: list[float]) -> float:
    return max(weights_raw) if weights_raw else 0.0


def max_sector_pct(holdings: list[Holding]) -> float:
    sector_sums: dict[str, float] = {}
    for h in holdings:
        if h.is_cash:
            continue
        sector = h.sector_theme if h.sector_theme else "未分类"
        sector_sums[sector] = sector_sums.get(sector, 0) + h.weight_pct / 100
    return max(sector_sums.values()) if sector_sums else 0.0


def compute_sector_exposure(holdings: list[Holding]) -> dict[str, float]:
    """Sector weights from non-cash holdings (raw fractions, sum = 1 - cash)."""
    non_cash = [h for h in holdings if not h.is_cash]
    sector_map: dict[str, float] = {}
    for h in non_cash:
        sector = h.sector_theme if h.sector_theme else "未分类"
        sector_map[sector] = sector_map.get(sector, 0) + h.weight_pct / 100
    return {s: round(v, 4) for s, v in sorted(sector_map.items())}


def compute_all_concentration(
    holdings: list[Holding],
) -> dict:
    weights_raw = [h.weight_pct / 100 for h in holdings if not h.is_cash]
    hhi = compute_hhi(weights_raw)
    return {
        "hhi": round(hhi, 4),
        "effective_n": round(effective_n(hhi), 2),
        "top_3_pct": round(top_3_pct(weights_raw), 4),
        "max_holding_pct": round(max_holding_pct(weights_raw), 4),
        "max_sector_pct": round(max_sector_pct(holdings), 4),
    }

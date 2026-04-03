"""Strategy templates: derivative strategies with permission requirements."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_CATALOG_PATH = Path(__file__).parent / "instrument_catalog.json"

STRATEGY_TEMPLATES: dict[str, dict[str, Any]] = {
    "hold_etf": {
        "requires_permission": None,
        "description": "买入持有ETF",
    },
    "protective_put": {
        "requires_permission": "option_account",
        "description": "买入虚值认沽期权保护下行",
    },
    "collar": {
        "requires_permission": "option_account",
        "description": "买入认沽+卖出认购，锁定收益区间",
    },
    "commodity_hedge": {
        "requires_permission": "futures_account",
        "description": "商品期货对冲",
    },
}


def load_catalog(path: str | Path | None = None) -> list[dict]:
    """Load instrument catalog."""
    p = Path(path) if path else _CATALOG_PATH
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def determine_access_req(
    candidate: dict,
    strategy: str,
    account_permissions: list[str],
) -> list[str]:
    """Return list of missing permissions (empty = all good)."""
    needed: set[str] = set()

    # Source 1: instrument itself
    mp = candidate.get("requires_market_permission")
    if mp:
        needed.add(mp)

    # Source 2: strategy template
    tmpl = STRATEGY_TEMPLATES.get(strategy, {})
    sp = tmpl.get("requires_permission")
    if sp:
        needed.add(sp)

    missing = sorted(needed - set(account_permissions))
    return missing


def is_investable(
    candidate: dict,
    strategy: str,
    constraints,  # UserConstraints
) -> tuple[bool, list[str]]:
    """Four-axis filter. Returns (can_invest, missing_permissions).

    Axis 1/2/2b: fail -> (False, [])
    Axis 3: fail -> (True, [missing perms]) -> Tier 3
    """
    # Axis 1: market
    if candidate.get("market") not in constraints.allowed_markets:
        return False, []

    # Axis 2: instrument type
    if candidate.get("instrument_type") not in constraints.allowed_instruments:
        return False, []

    # Axis 2b: exposure market
    exp = candidate.get("exposure_market")
    if exp and exp not in constraints.allowed_exposure:
        return False, []

    # Axis 3: permissions
    missing = determine_access_req(candidate, strategy, constraints.account_permissions)
    if missing:
        return True, missing  # Can invest but needs new permissions -> Tier 3

    return True, []

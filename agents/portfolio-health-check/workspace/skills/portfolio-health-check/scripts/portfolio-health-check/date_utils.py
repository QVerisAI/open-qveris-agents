"""Shared date utility functions."""
from __future__ import annotations

import calendar
from datetime import date


def shift_months(anchor: date, months: int) -> date:
    """Shift anchor date backward by N months, clamping to valid day."""
    month_index = anchor.month - months
    year = anchor.year + (month_index - 1) // 12
    month = (month_index - 1) % 12 + 1
    day = min(anchor.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def shift_years(anchor: date, years: int) -> date:
    """Shift anchor date backward by N years, clamping to valid day."""
    year = anchor.year - years
    day = min(anchor.day, calendar.monthrange(year, anchor.month)[1])
    return date(year, anchor.month, day)


def format_ymd(value: date) -> str:
    """Format date as YYYY-MM-DD."""
    return value.strftime("%Y-%m-%d")

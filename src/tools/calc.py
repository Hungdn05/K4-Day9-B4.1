"""Numeric and timestamp helpers.

Every money figure and every hour figure in the submission passes through here.
Decimal with ROUND_HALF_UP is used instead of float arithmetic because Python's
`round()` does banker's rounding (round(2.675, 2) == 2.67) and float sums drift
(0.1 + 0.2 == 0.30000000000000004), either of which would silently cost points.
"""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable

TS_FORMAT = "%Y-%m-%d %H:%M:%S"
_CENT = Decimal("0.01")


def round2(value: Decimal | float | int | str | None) -> float | None:
    """Round half-up to 2 decimals and return a JSON-friendly float."""
    if value is None:
        return None
    dec = value if isinstance(value, Decimal) else Decimal(str(value))
    return float(dec.quantize(_CENT, rounding=ROUND_HALF_UP))


def dec_sum(values: Iterable[str | float | None]) -> Decimal:
    """Exact sum of a column of money strings, skipping missing values."""
    total = Decimal("0")
    for value in values:
        if value is None or value == "":
            continue
        total += Decimal(str(value))
    return total


def parse_ts(value: str | None) -> datetime | None:
    """Parse a raw CSV timestamp. Returns None for missing/blank cells.

    No timezone conversion: README section 2 says timestamps are compared as-is.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "nat", "none", "null"}:
        return None
    try:
        return datetime.strptime(text, TS_FORMAT)
    except ValueError:
        # A few Olist cells carry sub-second precision; tolerate it.
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None


def clean_ts(value: str | None) -> str | None:
    """Echo a timestamp exactly as the schema wants it, or None if absent."""
    parsed = parse_ts(value)
    return parsed.strftime(TS_FORMAT) if parsed else None


def hours_between(later: str | None, earlier: str | None) -> float | None:
    """`later - earlier` expressed in hours, rounded half-up to 2 decimals.

    Positive means `later` happened after `earlier` (i.e. late). Returns None if
    either side is missing, which the schema represents as null.
    """
    a, b = parse_ts(later), parse_ts(earlier)
    if a is None or b is None:
        return None
    seconds = Decimal((a - b).total_seconds())
    return round2(seconds / Decimal(3600))

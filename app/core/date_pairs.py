"""Generate valid (outbound, return) date pairs from search windows.

A pair is valid when:
- the outbound date falls in the outbound window and its weekday is allowed;
- the return date falls in the return window and its weekday is allowed;
- the number of nights (return - outbound) is within [min_nights, max_nights];
- the return date is strictly after the outbound date.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class DateWindow:
    """A date range plus a set of allowed weekdays.

    Attributes:
        start: First date in the inclusive range.
        end: Last date in the inclusive range.
        weekdays: Allowed weekdays as a set of ints (Monday=0 ... Sunday=6).
            An empty set means "any weekday".
    """

    start: date
    end: date
    weekdays: frozenset[int] = frozenset()

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError("DateWindow.end must be >= start")
        bad = {w for w in self.weekdays if not 0 <= w <= 6}
        if bad:
            raise ValueError(f"weekdays must be in 0..6, got {bad}")

    def dates(self) -> list[date]:
        """Return the list of dates in the window matching the weekday filter."""
        out: list[date] = []
        d = self.start
        while d <= self.end:
            if not self.weekdays or d.weekday() in self.weekdays:
                out.append(d)
            d += timedelta(days=1)
        return out


def generate_date_pairs(
    outbound: DateWindow,
    return_: DateWindow,
    min_nights: int,
    max_nights: int,
) -> list[tuple[date, date]]:
    """Return all valid ``(outbound_date, return_date)`` pairs.

    Args:
        outbound: Outbound window (date range + allowed weekdays).
        return_: Return window (date range + allowed weekdays).
        min_nights: Minimum number of nights, inclusive. Must be >= 1.
        max_nights: Maximum number of nights, inclusive.

    Returns:
        A list of `(outbound_date, return_date)` tuples sorted by outbound
        date then return date.

    Raises:
        ValueError: If ``min_nights < 1`` or ``max_nights < min_nights``.
    """
    if min_nights < 1:
        raise ValueError("min_nights must be >= 1")
    if max_nights < min_nights:
        raise ValueError("max_nights must be >= min_nights")

    out_dates = outbound.dates()
    ret_dates = return_.dates()
    ret_set = set(ret_dates)
    pairs: list[tuple[date, date]] = []
    for o in out_dates:
        for n in range(min_nights, max_nights + 1):
            r = o + timedelta(days=n)
            if r in ret_set:
                pairs.append((o, r))
    pairs.sort()
    return pairs

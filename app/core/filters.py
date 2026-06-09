"""Pure filter helpers operating on lists of Flight objects.

The job runner pulls raw scrape results from cache and runs them through
these functions; nothing here touches the network or the DB.
"""

from __future__ import annotations

from collections.abc import Iterable

from .models import Flight, LegFilter


def filter_flights(flights: Iterable[Flight], leg: LegFilter) -> list[Flight]:
    """Return only those flights satisfying the per-leg filter."""
    return [f for f in flights if leg.passes(f)]


def cheapest(flights: Iterable[Flight]) -> Flight | None:
    """Return the cheapest flight in the iterable, or None if empty.

    Price is compared in the flight's own currency — callers must ensure
    they only mix flights of the same currency before calling, or convert
    upstream.
    """
    return min(flights, key=lambda f: f.price_amount, default=None)

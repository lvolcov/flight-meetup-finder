"""Meetup matching: pair two travellers' flights to a common destination.

A match is an itinerary where both travellers' outbound and return flights
satisfy their per-leg filters and the arrival/departure-gap constraints.
"""

from __future__ import annotations

from dataclasses import dataclass

from .filters import cheapest, filter_flights
from .models import Flight, LegFilter


def gap_minutes(a: Flight, b: Flight, *, at_arrival: bool) -> int:
    """Absolute minutes between two flights' arrival or departure times."""
    if at_arrival:
        delta = a.arrive_dt - b.arrive_dt
    else:
        delta = a.depart_dt - b.depart_dt
    return int(abs(delta.total_seconds()) // 60)


@dataclass(frozen=True)
class MeetupCandidate:
    """A matched meetup itinerary."""

    a_outbound: Flight
    a_return: Flight
    b_outbound: Flight
    b_return: Flight
    arrival_gap_minutes: int
    departure_gap_minutes: int
    combined_gbp: float


def _to_gbp(f: Flight, eur_to_gbp: float) -> float:
    if f.price_currency == "GBP":
        return f.price_amount
    if f.price_currency == "EUR":
        return f.price_amount * eur_to_gbp
    raise ValueError(f"Unsupported currency {f.price_currency!r}")


def match_meetup(
    a_outbound_options: list[Flight],
    a_return_options: list[Flight],
    b_outbound_options: list[Flight],
    b_return_options: list[Flight],
    *,
    a_outbound_filter: LegFilter,
    a_return_filter: LegFilter,
    b_outbound_filter: LegFilter,
    b_return_filter: LegFilter,
    max_arrival_gap_minutes: int | None,
    max_departure_gap_minutes: int | None,
    max_combined_gbp: float | None,
    eur_to_gbp: float,
) -> MeetupCandidate | None:
    """Find the cheapest valid meetup itinerary, if any.

    The function applies all leg filters, then picks the cheapest surviving
    option for each leg, then validates the gap constraints. If a gap
    constraint fails it returns None — by design we do **not** search the
    full cartesian product here; that lives in the job runner where caching
    cost is amortised.

    Args:
        a_outbound_options: All scraped options for traveller A's outbound.
        a_return_options: All scraped options for traveller A's return.
        b_outbound_options: All scraped options for traveller B's outbound.
        b_return_options: All scraped options for traveller B's return.
        a_outbound_filter: Per-leg filter for A's outbound.
        a_return_filter: Per-leg filter for A's return.
        b_outbound_filter: Per-leg filter for B's outbound.
        b_return_filter: Per-leg filter for B's return.
        max_arrival_gap_minutes: Max minutes between A's and B's arrivals
            at the destination, or None to skip the check.
        max_departure_gap_minutes: Max minutes between A's and B's return
            departures from the destination, or None to skip.
        max_combined_gbp: Optional combined-price cap in GBP.
        eur_to_gbp: Static EUR -> GBP conversion rate.

    Returns:
        A :class:`MeetupCandidate` or ``None`` if no valid itinerary exists.
    """
    a_out = cheapest(filter_flights(a_outbound_options, a_outbound_filter))
    a_ret = cheapest(filter_flights(a_return_options, a_return_filter))
    b_out = cheapest(filter_flights(b_outbound_options, b_outbound_filter))
    b_ret = cheapest(filter_flights(b_return_options, b_return_filter))
    if not (a_out and a_ret and b_out and b_ret):
        return None

    arr_gap = gap_minutes(a_out, b_out, at_arrival=True)
    dep_gap = gap_minutes(a_ret, b_ret, at_arrival=False)
    if max_arrival_gap_minutes is not None and arr_gap > max_arrival_gap_minutes:
        return None
    if max_departure_gap_minutes is not None and dep_gap > max_departure_gap_minutes:
        return None

    combined = (
        _to_gbp(a_out, eur_to_gbp)
        + _to_gbp(a_ret, eur_to_gbp)
        + _to_gbp(b_out, eur_to_gbp)
        + _to_gbp(b_ret, eur_to_gbp)
    )
    if max_combined_gbp is not None and combined > max_combined_gbp:
        return None

    return MeetupCandidate(
        a_outbound=a_out,
        a_return=a_ret,
        b_outbound=b_out,
        b_return=b_ret,
        arrival_gap_minutes=arr_gap,
        departure_gap_minutes=dep_gap,
        combined_gbp=round(combined, 2),
    )

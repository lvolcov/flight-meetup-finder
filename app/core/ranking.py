"""Ranking helpers for matched candidates.

Kept deliberately small — these are the sort keys used by both the server
when storing results and the client when re-sorting in the browser.
"""

from __future__ import annotations

from collections.abc import Iterable

from .matching import MeetupCandidate


def by_combined_price(candidates: Iterable[MeetupCandidate]) -> list[MeetupCandidate]:
    """Return candidates sorted ascending by combined GBP price."""
    return sorted(candidates, key=lambda c: c.combined_gbp)


def by_arrival_gap(candidates: Iterable[MeetupCandidate]) -> list[MeetupCandidate]:
    """Return candidates sorted ascending by arrival-gap minutes."""
    return sorted(candidates, key=lambda c: c.arrival_gap_minutes)


def by_total_duration(candidates: Iterable[MeetupCandidate]) -> list[MeetupCandidate]:
    """Return candidates sorted ascending by summed flight duration (4 legs)."""

    def total(c: MeetupCandidate) -> int:
        return (
            c.a_outbound.duration_minutes
            + c.a_return.duration_minutes
            + c.b_outbound.duration_minutes
            + c.b_return.duration_minutes
        )

    return sorted(candidates, key=total)

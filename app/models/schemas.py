"""Pydantic v2 request/response schemas for the HTTP layer.

Purpose: validate every request and shape every response (N-5), and translate
user-facing filter choices into the framework-free :mod:`app.core` dataclasses
used by the matching engine. Created 2026-06-09.
"""

from __future__ import annotations

from datetime import date, time
from typing import Literal

from pydantic import BaseModel, Field

from app.core.date_pairs import DateWindow
from app.core.models import LegFilter, Stops, TimeRule

Mode = Literal["meetup", "visit"]
Preset = Literal["morning", "afternoon", "evening", "any"]
StopsChoice = Literal["direct", "one", "any"]

_STOPS_MAP: dict[str, Stops] = {
    "direct": Stops.DIRECT,
    "one": Stops.ONE,
    "any": Stops.ANY,
}


class LegRule(BaseModel):
    """Time-of-day rule for a single leg (F-7).

    A custom ``depart_after`` / ``arrive_before`` overrides ``preset`` when
    either is supplied.
    """

    preset: Preset = "any"
    depart_after: time | None = None
    arrive_before: time | None = None

    def to_time_rule(self) -> TimeRule:
        """Build the engine :class:`TimeRule` for this leg."""
        if self.depart_after is not None or self.arrive_before is not None:
            return TimeRule(
                depart_after=self.depart_after, arrive_before=self.arrive_before
            )
        return TimeRule.preset(self.preset)


class TravellerFilters(BaseModel):
    """Per-traveller leg rules, max duration and max stops (F-7..F-9)."""

    outbound: LegRule = Field(default_factory=LegRule)
    return_: LegRule = Field(default_factory=LegRule, alias="return")
    max_duration_hours: float | None = None
    max_stops: StopsChoice = "any"

    model_config = {"populate_by_name": True}

    def _leg_filter(self, rule: LegRule) -> LegFilter:
        max_minutes = (
            int(self.max_duration_hours * 60)
            if self.max_duration_hours is not None
            else None
        )
        return LegFilter(
            time_rule=rule.to_time_rule(),
            max_duration_minutes=max_minutes,
            max_stops=_STOPS_MAP[self.max_stops],
        )

    def outbound_filter(self) -> LegFilter:
        """Return the :class:`LegFilter` for this traveller's outbound leg."""
        return self._leg_filter(self.outbound)

    def return_filter(self) -> LegFilter:
        """Return the :class:`LegFilter` for this traveller's return leg."""
        return self._leg_filter(self.return_)


class SearchRequest(BaseModel):
    """A search submission (F-4..F-12, F-20)."""

    mode: Mode
    outbound_start: date
    outbound_end: date
    outbound_weekdays: list[int] = Field(default_factory=list)
    return_start: date
    return_end: date
    return_weekdays: list[int] = Field(default_factory=list)
    min_nights: int = 2
    max_nights: int = 5

    # Meetup: candidate destination subset (None = all enabled).
    # Visit: the Portugal target airports (defaults to LIS in the runner).
    destinations: list[str] | None = None
    b_origins: list[str] = Field(default_factory=lambda: ["LIS"])

    traveller_a: TravellerFilters = Field(default_factory=TravellerFilters)
    traveller_b: TravellerFilters | None = None

    max_arrival_gap_hours: float | None = None
    max_departure_gap_hours: float | None = None
    max_combined_gbp: float | None = None
    max_price_gbp: float | None = None
    hidden_city: bool = False

    def outbound_window(self) -> DateWindow:
        """Build the outbound :class:`DateWindow`."""
        return DateWindow(
            self.outbound_start,
            self.outbound_end,
            frozenset(self.outbound_weekdays),
        )

    def return_window(self) -> DateWindow:
        """Build the return :class:`DateWindow`."""
        return DateWindow(
            self.return_start, self.return_end, frozenset(self.return_weekdays)
        )


# --------------------------------------------------------------------------- #
# Responses
# --------------------------------------------------------------------------- #
class Estimate(BaseModel):
    """Query-count and duration estimate for a (would-be) search."""

    estimated_queries: int
    # Queries not already in a fresh cache — the ones that actually cost time.
    uncached_queries: int
    estimated_seconds: int


class JobCreated(BaseModel):
    """Response to ``POST /api/search``."""

    job_id: str
    estimated_queries: int
    uncached_queries: int = 0
    estimated_seconds: int = 0


class RerunCheck(BaseModel):
    """Pre-flight info for re-running an old search."""

    dates_in_past: bool
    outbound_end: str
    estimated_queries: int
    uncached_queries: int
    estimated_seconds: int


class SaveFromJob(BaseModel):
    """Payload to save a finished job's filters as a named search."""

    name: str


class JobSummary(BaseModel):
    """One row in the recent-searches list (``GET /api/jobs``)."""

    id: str
    mode: str
    status: str
    queries_total: int
    queries_done: int
    queries_failed: int
    created_at: str


class JobStatus(BaseModel):
    """Response to ``GET /api/jobs/{id}`` — status, counts and results."""

    id: str
    mode: str
    status: str
    queries_total: int
    queries_done: int
    queries_failed: int
    created_at: str = ""
    error: str | None = None
    results: list[dict] = Field(default_factory=list)
    hidden_city: list[dict] = Field(default_factory=list)


class Destination(BaseModel):
    """A configurable candidate destination."""

    iata: str
    name: str
    enabled: bool = True
    # Schengen airports have no passport control from Lisbon (Talita's
    # constraint); non-Schengen ones can be deselected in bulk in the UI.
    schengen: bool = True


class DestinationCreate(BaseModel):
    """Payload to add a destination by IATA code."""

    iata: str
    name: str | None = None


class DestinationUpdate(BaseModel):
    """Payload to toggle a destination's enabled flag."""

    enabled: bool


class SavedSearchCreate(BaseModel):
    """Payload to persist a named filter set (F-23)."""

    name: str
    request: SearchRequest


class SavedSearch(BaseModel):
    """A stored saved search."""

    id: int
    name: str
    mode: str
    filters_json: dict
    created_at: str
    last_run_at: str | None = None
    last_job_id: str | None = None


class FoundFlight(BaseModel):
    """A flight collected into the persistent, de-duplicated Found list (F-36).

    ``payload`` carries the same itinerary shape the results view renders, so
    the frontend can reuse the result card. ``check_*`` fields hold the latest
    availability re-check (``check_status`` is ``available`` / ``gone`` /
    ``error``; ``check_price_gbp`` is the cheapest combined fare found now).
    """

    id: int
    mode: str
    destination: str
    b_origin: str | None = None
    outbound_date: str
    return_date: str
    combined_gbp: float
    payload: dict
    first_seen_at: str
    last_seen_at: str
    checked_at: str | None = None
    check_status: str | None = None
    check_note: str | None = None
    check_price_gbp: float | None = None

"""In-memory dataclasses for flights, itineraries and filters.

These are the canonical shapes used by the matching engine. They are kept
separate from the Pydantic request/response models so the engine never has
to depend on FastAPI/Pydantic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from enum import Enum


class Stops(str, Enum):
    """Allowed stop counts for a single leg."""

    DIRECT = "direct"
    ONE = "one"
    ANY = "any"

    def allows(self, stops: int) -> bool:
        """Return True if a flight with ``stops`` connections is allowed."""
        if self is Stops.DIRECT:
            return stops == 0
        if self is Stops.ONE:
            return stops <= 1
        return True


@dataclass(frozen=True)
class TimeRule:
    """Allowed departure/arrival window for a leg.

    Either or both bounds may be ``None`` meaning "no constraint on that
    side". A flight passes when ``depart_after <= depart_time`` and
    ``arrive_time <= arrive_before`` (inclusive).
    """

    depart_after: time | None = None
    arrive_before: time | None = None

    @classmethod
    def preset(cls, name: str) -> "TimeRule":
        """Build a TimeRule from a preset name.

        Args:
            name: ``'morning' | 'afternoon' | 'evening' | 'any'``.
        """
        n = name.lower()
        if n == "morning":
            return cls(time(6, 0), time(12, 0))
        if n == "afternoon":
            return cls(time(12, 0), time(18, 0))
        if n == "evening":
            return cls(time(18, 0), time(23, 59))
        if n == "any":
            return cls()
        raise ValueError(f"unknown preset {name!r}")

    def passes(self, depart_dt: datetime, arrive_dt: datetime) -> bool:
        """Check whether the leg's depart/arrive datetimes satisfy the rule."""
        if self.depart_after is not None and depart_dt.time() < self.depart_after:
            return False
        if self.arrive_before is not None and arrive_dt.time() > self.arrive_before:
            return False
        return True


@dataclass(frozen=True)
class Flight:
    """A single normalised flight option returned by the scraper wrapper."""

    airline: str
    depart_dt: datetime
    arrive_dt: datetime
    duration_minutes: int
    stops: int
    price_amount: float
    price_currency: str  # 'GBP' or 'EUR'
    is_best: bool = False


@dataclass(frozen=True)
class LegFilter:
    """All per-leg filters that apply to a single flight option."""

    time_rule: TimeRule = field(default_factory=TimeRule)
    max_duration_minutes: int | None = None
    max_stops: Stops = Stops.ANY

    def passes(self, f: Flight) -> bool:
        """Return True if ``f`` satisfies every constraint on this leg."""
        if not self.max_stops.allows(f.stops):
            return False
        if (
            self.max_duration_minutes is not None
            and f.duration_minutes > self.max_duration_minutes
        ):
            return False
        if not self.time_rule.passes(f.depart_dt, f.arrive_dt):
            return False
        return True

"""Read-through scrape cache (F-19).

Purpose: serve a ``(origin, destination, date)`` flight list from SQLite when
it is fresher than ``CACHE_TTL_HOURS``; otherwise scrape via the
:class:`FlightsService`, throttle (``SCRAPE_DELAY_SECONDS`` ± 30% jitter) and
store the result. Created 2026-06-09.
"""

from __future__ import annotations

import asyncio
import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.config import Settings
from app.core.models import Flight
from app.services import db
from app.services.flights import (
    FlightsService,
    flight_from_dict,
    flight_to_dict,
)


def _is_fresh(fetched_at: str, ttl_hours: int) -> bool:
    """Return True if an ISO timestamp is within ``ttl_hours`` of now."""
    try:
        ts = datetime.fromisoformat(fetched_at)
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return datetime.now(UTC) - ts < timedelta(hours=ttl_hours)


async def _throttle(settings: Settings) -> None:
    """Sleep the configured scrape delay with ±30% jitter."""
    base = settings.scrape_delay_seconds
    if base <= 0:
        return
    await asyncio.sleep(base * random.uniform(0.7, 1.3))


async def get_flights_cached(
    db_path: Path,
    service: FlightsService,
    settings: Settings,
    origin: str,
    destination: str,
    flight_date: str,
) -> list[Flight]:
    """Return flights for one leg, using the cache when fresh.

    On a cache miss the scraper is called, the result is throttled and stored,
    then returned. ``flight_date`` is an ISO ``YYYY-MM-DD`` string.

    Raises:
        Exception: Propagates scraper errors so the job runner can apply its
            retry/fail-and-continue policy (F-16).
    """
    cached = await db.get_cache(db_path, origin, destination, flight_date)
    if cached and _is_fresh(cached["fetched_at"], settings.cache_ttl_hours):
        payload = json.loads(cached["payload"])
        return [flight_from_dict(item) for item in payload]

    parsed_date = datetime.fromisoformat(flight_date).date()
    flights = await service.search_one_way(origin, destination, parsed_date)
    await _throttle(settings)
    await db.put_cache(
        db_path,
        origin,
        destination,
        flight_date,
        [flight_to_dict(f) for f in flights],
    )
    return flights

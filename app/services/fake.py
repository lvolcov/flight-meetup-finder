"""Deterministic in-process flight source for e2e tests and demos.

Purpose: a :class:`~app.services.flights.FlightsService` that never touches the
network, so the container can be exercised end to end (and Playwright e2e tests
run) without scraping Google Flights. Enabled via ``FMF_FAKE_SCRAPER=1``.
Created 2026-06-09.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from app.core.models import Flight


class DeterministicFlightsService:
    """Return a small, stable set of flights derived from the search inputs."""

    async def search_one_way(
        self, origin: str, destination: str, flight_date: date
    ) -> list[Flight]:
        """Return three reproducible options for any one-way query."""
        currency = "GBP" if origin == "MAN" else "EUR"
        base = datetime.combine(flight_date, datetime.min.time())
        # Cheap fares cluster around midday so meetup arrival gaps stay small.
        specs = [
            ("TestAir", 11, 13, 120, 0, 90.0),
            ("MockJet", 9, 12, 180, 1, 70.0),
            ("DemoWings", 18, 20, 110, 0, 130.0),
        ]
        flights: list[Flight] = []
        for airline, dep_h, arr_h, dur, stops, price in specs:
            flights.append(
                Flight(
                    airline=airline,
                    depart_dt=base + timedelta(hours=dep_h),
                    arrive_dt=base + timedelta(hours=arr_h),
                    duration_minutes=dur,
                    stops=stops,
                    price_amount=price,
                    price_currency=currency,
                    is_best=airline == "TestAir",
                )
            )
        return flights

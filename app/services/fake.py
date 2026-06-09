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
        """Return three reproducible options that vary per destination.

        Prices and durations are derived from the destination code so that
        different destinations produce genuinely different rankings — which
        makes client-side re-sort observable in the UI and e2e tests.
        """
        currency = "GBP" if origin == "MAN" else "EUR"
        base = datetime.combine(flight_date, datetime.min.time())
        seed = sum(ord(c) for c in destination)
        price_base = 60 + seed % 80
        dur_base = 90 + seed % 60
        # Fares cluster around midday so meetup arrival gaps stay small.
        specs = [
            ("TestAir", 11, 13, dur_base, 0, price_base),
            ("MockJet", 9, 12, dur_base + 60, 1, price_base - 15),
            ("DemoWings", 18, 20, dur_base - 10, 0, price_base + 45),
        ]
        flights: list[Flight] = []
        for airline, dep_h, arr_h, dur, stops, price in specs:
            flights.append(
                Flight(
                    airline=airline,
                    depart_dt=base + timedelta(hours=dep_h),
                    arrive_dt=base + timedelta(hours=arr_h),
                    duration_minutes=max(30, dur),
                    stops=stops,
                    price_amount=float(price),
                    price_currency=currency,
                    is_best=airline == "TestAir",
                )
            )
        return flights

"""Tests for task expansion, query estimation and a mocked end-to-end job.

The scraper is replaced by a deterministic fake (no Google Flights calls).
These exercise the streaming-results path, the EUR->GBP combine and the
fail-soft retry policy (F-16). Created 2026-06-09.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from app.config import Settings
from app.core.models import Flight
from app.models.schemas import SearchRequest, TravellerFilters
from app.services import db
from app.services.jobs import JobRunner, estimate_queries, expand_tasks


def _settings() -> Settings:
    return Settings(scrape_delay_seconds=0, cache_ttl_hours=12, eur_to_gbp=0.85)


class FakeService:
    """Deterministic flight source: one flight per leg, arriving at noon."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def search_one_way(
        self, origin: str, destination: str, flight_date: date
    ) -> list[Flight]:
        self.calls.append((origin, destination, flight_date.isoformat()))
        currency = "GBP" if origin == "MAN" else "EUR"
        return [
            Flight(
                airline="TestAir",
                depart_dt=datetime.combine(flight_date, datetime.min.time()).replace(
                    hour=10
                ),
                arrive_dt=datetime.combine(flight_date, datetime.min.time()).replace(
                    hour=12
                ),
                duration_minutes=120,
                stops=0,
                price_amount=100.0,
                price_currency=currency,
            )
        ]


class BrokenService:
    """Always raises — drives the retry/fail-soft path."""

    async def search_one_way(
        self, origin: str, destination: str, flight_date: date
    ) -> list[Flight]:
        raise RuntimeError("scraper down")


class ConcurrencyProbeService(FakeService):
    """FakeService that records the peak number of overlapping scrapes."""

    def __init__(self) -> None:
        super().__init__()
        self.in_flight = 0
        self.max_in_flight = 0

    async def search_one_way(
        self, origin: str, destination: str, flight_date: date
    ) -> list[Flight]:
        import asyncio

        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            # A real (tiny) sleep guarantees concurrent scrapes genuinely
            # overlap rather than relying on a single scheduler yield.
            await asyncio.sleep(0.02)
            return await super().search_one_way(origin, destination, flight_date)
        finally:
            self.in_flight -= 1


def _meetup_request() -> SearchRequest:
    return SearchRequest(
        mode="meetup",
        outbound_start=date(2026, 7, 10),
        outbound_end=date(2026, 7, 10),
        return_start=date(2026, 7, 13),
        return_end=date(2026, 7, 13),
        min_nights=3,
        max_nights=3,
        destinations=["BCN"],
        b_origins=["LIS"],
        traveller_a=TravellerFilters(),
        traveller_b=TravellerFilters(),
    )


def test_expand_meetup_task_count() -> None:
    req = _meetup_request()
    tasks, tuples = expand_tasks(req, "MAN", ["BCN"])
    # One date pair, one destination, one b-origin -> 4 distinct legs, 1 tuple.
    assert len(tuples) == 1
    assert len(tasks) == 4
    assert ("MAN", "BCN", "2026-07-10") in tasks
    assert ("BCN", "LIS", "2026-07-13") in tasks


def test_expand_visit_task_count() -> None:
    req = SearchRequest(
        mode="visit",
        outbound_start=date(2026, 7, 10),
        outbound_end=date(2026, 7, 10),
        return_start=date(2026, 7, 13),
        return_end=date(2026, 7, 13),
        min_nights=3,
        max_nights=3,
        destinations=["LIS"],
    )
    tasks, tuples = expand_tasks(req, "MAN", ["LIS"])
    assert len(tuples) == 1
    assert tasks == {("MAN", "LIS", "2026-07-10"), ("LIS", "MAN", "2026-07-13")}


async def test_schengen_column_migrates_old_database(tmp_path: Path) -> None:
    """A DB created before the schengen column exists must be upgraded."""
    import aiosqlite

    db_path = tmp_path / "fmf.db"
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "CREATE TABLE destinations (iata TEXT PRIMARY KEY, name TEXT NOT "
            "NULL, enabled INTEGER NOT NULL DEFAULT 1, added_at TEXT NOT NULL)"
        )
        await conn.execute(
            "INSERT INTO destinations VALUES ('EDI', 'Edinburgh', 1, 'x'), "
            "('BCN', 'Barcelona', 1, 'x')"
        )
        await conn.commit()

    await db.init_db(db_path)
    rows = {r["iata"]: r for r in await db.list_destinations(db_path)}
    assert rows["EDI"]["schengen"] == 0
    assert rows["BCN"]["schengen"] == 1


async def test_estimate_queries(tmp_path: Path) -> None:
    db_path = tmp_path / "fmf.db"
    await db.init_db(db_path)
    assert await estimate_queries(db_path, _meetup_request(), "MAN") == 4


async def test_meetup_job_streams_results(tmp_path: Path) -> None:
    db_path = tmp_path / "fmf.db"
    await db.init_db(db_path)
    req = _meetup_request()
    await db.create_job(db_path, "job1", "meetup", req.model_dump_json(), 4)

    service = FakeService()
    runner = JobRunner(db_path, service, _settings())
    await runner._process_job("job1")

    job = await db.get_job(db_path, "job1")
    assert job is not None
    assert job["status"] == "done"
    assert job["queries_done"] == 4
    assert job["queries_failed"] == 0

    results = await db.list_results(db_path, "job1")
    assert len(results) == 1
    payload = results[0]["payload"]
    assert payload["kind"] == "meetup"
    # Legs: A-out MAN->BCN GBP 100; A-ret/B-out/B-ret originate elsewhere ->
    # EUR 100 @ 0.85 = 85 each. 100 + 85 + 85 + 85 = 355.
    assert payload["combined_gbp"] == pytest.approx(355.0)
    # Every individual leg carries its own GBP price for the UI.
    assert payload["traveller_a"]["outbound"]["price_gbp"] == pytest.approx(100.0)
    assert payload["traveller_a"]["return"]["price_gbp"] == pytest.approx(85.0)
    assert payload["traveller_b"]["outbound"]["price_gbp"] == pytest.approx(85.0)
    assert payload["traveller_b"]["return"]["price_gbp"] == pytest.approx(85.0)
    assert payload["arrival_gap_minutes"] == 0


async def test_job_survives_scraper_failure(tmp_path: Path) -> None:
    db_path = tmp_path / "fmf.db"
    await db.init_db(db_path)
    req = _meetup_request()
    await db.create_job(db_path, "job2", "meetup", req.model_dump_json(), 4)

    runner = JobRunner(db_path, BrokenService(), _settings())
    await runner._process_job("job2")

    job = await db.get_job(db_path, "job2")
    assert job is not None
    assert job["status"] == "done"  # F-16: no single failure kills the job
    assert job["queries_failed"] == 4
    assert await db.list_results(db_path, "job2") == []


@pytest.mark.parametrize("concurrency", [1, 2])
async def test_scrape_concurrency_is_bounded(
    tmp_path: Path, concurrency: int
) -> None:
    """Legs scrape in parallel, but never more than ``scrape_concurrency``."""
    db_path = tmp_path / "fmf.db"
    await db.init_db(db_path)
    req = _meetup_request()  # one date pair -> 4 distinct legs
    await db.create_job(db_path, "jobc", "meetup", req.model_dump_json(), 4)

    service = ConcurrencyProbeService()
    settings = Settings(
        scrape_delay_seconds=0, cache_ttl_hours=12, scrape_concurrency=concurrency
    )
    runner = JobRunner(db_path, service, settings)
    await runner._process_job("jobc")

    job = await db.get_job(db_path, "jobc")
    assert job is not None and job["status"] == "done"
    assert job["queries_done"] == 4
    assert service.max_in_flight == concurrency  # hits the cap, never exceeds


async def test_visit_job_with_price_cap(tmp_path: Path) -> None:
    db_path = tmp_path / "fmf.db"
    await db.init_db(db_path)
    req = SearchRequest(
        mode="visit",
        outbound_start=date(2026, 7, 10),
        outbound_end=date(2026, 7, 10),
        return_start=date(2026, 7, 13),
        return_end=date(2026, 7, 13),
        min_nights=3,
        max_nights=3,
        destinations=["LIS"],
        max_price_gbp=50.0,  # MAN->LIS 100 + LIS->MAN 85 = 185 > 50 -> no match
    )
    await db.create_job(db_path, "job3", "visit", req.model_dump_json(), 2)
    runner = JobRunner(db_path, FakeService(), _settings())
    await runner._process_job("job3")
    assert await db.list_results(db_path, "job3") == []

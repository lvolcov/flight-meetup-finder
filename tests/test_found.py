"""Tests for the Found-flights feature (F-36).

Covers the price-independent de-dup signature, the availability re-check
(cheapest-now + price delta, and the "gone" case) and the runner auto-capturing
matches into the persistent, de-duplicated found_flights table. The scraper is
always a deterministic fake — no Google Flights calls. Created 2026-06-12.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from app.config import Settings
from app.core.models import Flight
from app.models.schemas import SearchRequest, TravellerFilters
from app.services import db
from app.services.found import check_availability, found_signature
from app.services.jobs import JobRunner


def _settings(**overrides: object) -> Settings:
    base: dict = {
        "scrape_delay_seconds": 0,
        "cache_ttl_hours": 12,
        "eur_to_gbp": 0.85,
        "scrape_concurrency": 2,
    }
    base.update(overrides)
    return Settings(**base)


def _payload(combined: float = 355.0, airline: str = "TestAir",
             depart: str = "2026-07-10T10:00:00") -> dict:
    """A meetup result payload (BCN, MAN + LIS travellers)."""
    return {
        "kind": "meetup",
        "destination": "BCN",
        "b_origin": "LIS",
        "outbound_date": "2026-07-10",
        "return_date": "2026-07-13",
        "combined_gbp": combined,
        "traveller_a": {
            "name": "Lucas", "origin": "MAN",
            "outbound": {"airline": airline, "origin": "MAN",
                         "destination": "BCN", "depart_dt": depart},
            "return": {"airline": "TestAir", "origin": "BCN",
                       "destination": "MAN", "depart_dt": "2026-07-13T10:00:00"},
        },
        "traveller_b": {
            "name": "Talita", "origin": "LIS",
            "outbound": {"airline": "TestAir", "origin": "LIS",
                         "destination": "BCN", "depart_dt": "2026-07-10T10:00:00"},
            "return": {"airline": "TestAir", "origin": "BCN",
                       "destination": "LIS", "depart_dt": "2026-07-13T10:00:00"},
        },
    }


class _StubService:
    """Returns one flight per leg; configurable price and empty routes."""

    def __init__(self, price: float = 100.0,
                 empty: set[tuple[str, str]] | None = None) -> None:
        self.price = price
        self.empty = empty or set()

    async def search_one_way(
        self, origin: str, destination: str, flight_date: date
    ) -> list[Flight]:
        if (origin, destination) in self.empty:
            return []
        base = datetime.combine(flight_date, datetime.min.time())
        return [
            Flight(
                airline="TestAir",
                depart_dt=base.replace(hour=10),
                arrive_dt=base.replace(hour=12),
                duration_minutes=120,
                stops=0,
                price_amount=self.price,
                price_currency="GBP" if origin == "MAN" else "EUR",
            )
        ]


class _FakeService(_StubService):
    """Alias used for the runner auto-capture test (records nothing extra)."""


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


# --------------------------------------------------------------------------- #
# Signature
# --------------------------------------------------------------------------- #
def test_signature_is_price_independent() -> None:
    assert found_signature(_payload(combined=300.0)) == found_signature(
        _payload(combined=999.0)
    )


def test_signature_distinguishes_flights() -> None:
    base = found_signature(_payload())
    assert base != found_signature(_payload(airline="OtherAir"))
    assert base != found_signature(_payload(depart="2026-07-10T18:00:00"))


# --------------------------------------------------------------------------- #
# Availability re-check
# --------------------------------------------------------------------------- #
async def test_check_availability_same_price() -> None:
    # MAN->BCN £100 (GBP) + 3 EUR legs @ 100*0.85 = 255 -> £355 combined now.
    out = await check_availability(_StubService(), _settings(), _payload(), 355.0)
    assert out["status"] == "available"
    assert out["price_gbp"] == pytest.approx(355.0)
    assert "same" in out["note"].lower()


async def test_check_availability_price_went_up() -> None:
    out = await check_availability(_StubService(), _settings(), _payload(), 300.0)
    assert out["status"] == "available"
    assert "up £55" in out["note"]


async def test_check_availability_gone() -> None:
    svc = _StubService(empty={("LIS", "BCN")})
    out = await check_availability(svc, _settings(), _payload(), 355.0)
    assert out["status"] == "gone"
    assert out["price_gbp"] is None


# --------------------------------------------------------------------------- #
# Auto-capture + de-dup
# --------------------------------------------------------------------------- #
async def test_job_auto_captures_and_dedups(tmp_path: Path) -> None:
    db_path = tmp_path / "fmf.db"
    await db.init_db(db_path)
    req = _meetup_request()
    runner = JobRunner(db_path, _FakeService(), _settings())

    await db.create_job(db_path, "j1", "meetup", req.model_dump_json(), 4)
    await runner._process_job("j1")
    found = await db.list_found_flights(db_path)
    assert len(found) == 1
    first_seen = found[0]["first_seen_at"]

    # A second identical search must not duplicate the found flight.
    await db.create_job(db_path, "j2", "meetup", req.model_dump_json(), 4)
    await runner._process_job("j2")
    found_again = await db.list_found_flights(db_path)
    assert len(found_again) == 1
    assert found_again[0]["first_seen_at"] == first_seen  # preserved

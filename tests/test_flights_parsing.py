"""Unit tests for the fast-flights wrapper's string parsers.

The scraper itself is never called here — we feed the parsers the exact string
shapes the library returns (verified 2026-06-09) and assert the normalised
output. Created 2026-06-09.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import pytest

from app.services.flights import (
    flight_from_dict,
    flight_to_dict,
    google_flights_link,
    normalise_flight,
    parse_duration,
    parse_flight_datetime,
    parse_price,
)


@dataclass
class FakeRaw:
    """Stand-in for a fast_flights Flight object."""

    name: str
    departure: str
    arrival: str
    duration: str
    stops: int
    price: str
    is_best: bool = False
    arrival_time_ahead: str = ""


@pytest.mark.parametrize(
    ("text", "minutes"),
    [
        ("3 hr 15 min", 195),
        ("2 hr", 120),
        ("45 min", 45),
        ("1 hr 5 min", 65),
        ("", 0),
    ],
)
def test_parse_duration(text: str, minutes: int) -> None:
    assert parse_duration(text) == minutes


@pytest.mark.parametrize(
    ("text", "amount", "currency"),
    [
        ("£99", 99.0, "GBP"),
        ("€120", 120.0, "EUR"),
        ("£1,234", 1234.0, "GBP"),
        ("€1,000.50", 1000.50, "EUR"),
    ],
)
def test_parse_price(text: str, amount: float, currency: str) -> None:
    assert parse_price(text) == (amount, currency)


def test_parse_price_rejects_empty() -> None:
    with pytest.raises(ValueError):
        parse_price("free")


def test_parse_flight_datetime_basic() -> None:
    anchor = date(2026, 7, 9)
    dt = parse_flight_datetime("8:35 PM on Thu, Jul 9", anchor)
    assert dt == datetime(2026, 7, 9, 20, 35)


def test_parse_flight_datetime_year_wrap() -> None:
    # A December search landing in January must roll the year forward.
    anchor = date(2026, 12, 30)
    dt = parse_flight_datetime("1:00 AM on Fri, Jan 1", anchor)
    assert dt == datetime(2027, 1, 1, 1, 0)


def test_parse_flight_datetime_no_date_uses_anchor() -> None:
    anchor = date(2026, 7, 9)
    dt = parse_flight_datetime("6:05 AM", anchor)
    assert dt == datetime(2026, 7, 9, 6, 5)


def test_normalise_flight_same_day() -> None:
    raw = FakeRaw(
        name="easyJet",
        departure="8:35 PM on Thu, Jul 9",
        arrival="11:50 PM on Thu, Jul 9",
        duration="3 hr 15 min",
        stops=0,
        price="£99",
        is_best=True,
    )
    flight = normalise_flight(raw, date(2026, 7, 9))
    assert flight.airline == "easyJet"
    assert flight.depart_dt == datetime(2026, 7, 9, 20, 35)
    assert flight.arrive_dt == datetime(2026, 7, 9, 23, 50)
    assert flight.duration_minutes == 195
    assert flight.stops == 0
    assert (flight.price_amount, flight.price_currency) == (99.0, "GBP")
    assert flight.is_best is True


def test_normalise_flight_overnight_via_ahead_flag() -> None:
    raw = FakeRaw(
        name="TAP",
        departure="11:30 PM on Thu, Jul 9",
        arrival="1:15 AM on Thu, Jul 9",
        duration="1 hr 45 min",
        stops=0,
        price="€120",
        arrival_time_ahead="+1",
    )
    flight = normalise_flight(raw, date(2026, 7, 9))
    assert flight.arrive_dt == datetime(2026, 7, 10, 1, 15)
    assert flight.arrive_dt > flight.depart_dt


def test_normalise_flight_overnight_without_flag() -> None:
    # No +1 flag, but arrival parses earlier than departure -> add a day.
    raw = FakeRaw(
        name="Ryanair",
        departure="11:30 PM on Thu, Jul 9",
        arrival="1:15 AM on Thu, Jul 9",
        duration="1 hr 45 min",
        stops=0,
        price="€80",
    )
    flight = normalise_flight(raw, date(2026, 7, 9))
    assert flight.arrive_dt == datetime(2026, 7, 10, 1, 15)


def test_flight_dict_round_trip() -> None:
    raw = FakeRaw(
        name="Vueling",
        departure="9:00 AM on Mon, Aug 3",
        arrival="11:40 AM on Mon, Aug 3",
        duration="2 hr 40 min",
        stops=1,
        price="€150",
    )
    flight = normalise_flight(raw, date(2026, 8, 3))
    assert flight_from_dict(flight_to_dict(flight)) == flight


def test_google_flights_link() -> None:
    link = google_flights_link("MAN", "LIS", date(2026, 7, 9))
    assert link.startswith("https://www.google.com/travel/flights?q=")
    assert "MAN" in link and "LIS" in link and "2026-07-09" in link
    assert " " not in link

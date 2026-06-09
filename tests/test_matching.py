from datetime import datetime, timedelta

from app.core.matching import gap_minutes, match_meetup
from app.core.models import Flight, LegFilter, Stops, TimeRule
from app.core.ranking import by_combined_price


def f(hour: int, minute: int = 0, *, duration: int = 120, stops: int = 0,
      price: float = 100.0, cur: str = "GBP", airline: str = "AA",
      day: int = 1) -> Flight:
    dep = datetime(2026, 7, day, hour, minute)
    return Flight(
        airline=airline,
        depart_dt=dep,
        arrive_dt=dep + timedelta(minutes=duration),
        duration_minutes=duration,
        stops=stops,
        price_amount=price,
        price_currency=cur,
    )


def test_gap_minutes_arrival():
    a = f(10, duration=120)   # arr 12:00
    b = f(8, duration=180)    # arr 11:00
    assert gap_minutes(a, b, at_arrival=True) == 60


def test_match_meetup_happy_path():
    cand = match_meetup(
        a_outbound_options=[f(10, duration=180, price=120, cur="GBP")],   # arr 13:00
        a_return_options=[f(18, duration=180, price=130, cur="GBP", day=5)],
        b_outbound_options=[f(11, duration=120, price=90, cur="EUR")],    # arr 13:00
        b_return_options=[f(18, duration=120, price=100, cur="EUR", day=5)],
        a_outbound_filter=LegFilter(),
        a_return_filter=LegFilter(),
        b_outbound_filter=LegFilter(),
        b_return_filter=LegFilter(),
        max_arrival_gap_minutes=60,
        max_departure_gap_minutes=60,
        max_combined_gbp=None,
        eur_to_gbp=0.85,
    )
    assert cand is not None
    assert cand.arrival_gap_minutes == 0
    assert cand.departure_gap_minutes == 0
    # 120 + 130 + (90 + 100) * 0.85 = 250 + 161.5 = 411.5
    assert cand.combined_gbp == 411.5


def test_match_meetup_arrival_gap_rejection():
    cand = match_meetup(
        a_outbound_options=[f(10, duration=180)],         # arr 13:00
        a_return_options=[f(18, duration=180, day=5)],
        b_outbound_options=[f(18, duration=120)],         # arr 20:00 — gap 7h
        b_return_options=[f(18, duration=120, day=5)],
        a_outbound_filter=LegFilter(),
        a_return_filter=LegFilter(),
        b_outbound_filter=LegFilter(),
        b_return_filter=LegFilter(),
        max_arrival_gap_minutes=180,
        max_departure_gap_minutes=None,
        max_combined_gbp=None,
        eur_to_gbp=0.85,
    )
    assert cand is None


def test_match_meetup_filter_eliminates_options():
    # Only direct flights allowed, but A's only option has stops=1
    cand = match_meetup(
        a_outbound_options=[f(10, stops=1)],
        a_return_options=[f(18, day=5)],
        b_outbound_options=[f(11)],
        b_return_options=[f(18, day=5)],
        a_outbound_filter=LegFilter(max_stops=Stops.DIRECT),
        a_return_filter=LegFilter(),
        b_outbound_filter=LegFilter(),
        b_return_filter=LegFilter(),
        max_arrival_gap_minutes=None,
        max_departure_gap_minutes=None,
        max_combined_gbp=None,
        eur_to_gbp=0.85,
    )
    assert cand is None


def test_match_meetup_price_cap():
    cand = match_meetup(
        a_outbound_options=[f(10, price=500)],
        a_return_options=[f(18, price=500, day=5)],
        b_outbound_options=[f(11, price=500, cur="EUR")],
        b_return_options=[f(18, price=500, cur="EUR", day=5)],
        a_outbound_filter=LegFilter(),
        a_return_filter=LegFilter(),
        b_outbound_filter=LegFilter(),
        b_return_filter=LegFilter(),
        max_arrival_gap_minutes=None,
        max_departure_gap_minutes=None,
        max_combined_gbp=500.0,
        eur_to_gbp=0.85,
    )
    assert cand is None


def test_ranking_by_price():
    c1 = match_meetup(
        a_outbound_options=[f(10, price=100)],
        a_return_options=[f(18, price=100, day=5)],
        b_outbound_options=[f(11, price=100, cur="EUR")],
        b_return_options=[f(18, price=100, cur="EUR", day=5)],
        a_outbound_filter=LegFilter(),
        a_return_filter=LegFilter(),
        b_outbound_filter=LegFilter(),
        b_return_filter=LegFilter(),
        max_arrival_gap_minutes=None,
        max_departure_gap_minutes=None,
        max_combined_gbp=None,
        eur_to_gbp=0.85,
    )
    c2 = match_meetup(
        a_outbound_options=[f(10, price=200)],
        a_return_options=[f(18, price=200, day=5)],
        b_outbound_options=[f(11, price=200, cur="EUR")],
        b_return_options=[f(18, price=200, cur="EUR", day=5)],
        a_outbound_filter=LegFilter(),
        a_return_filter=LegFilter(),
        b_outbound_filter=LegFilter(),
        b_return_filter=LegFilter(),
        max_arrival_gap_minutes=None,
        max_departure_gap_minutes=None,
        max_combined_gbp=None,
        eur_to_gbp=0.85,
    )
    ranked = by_combined_price([c2, c1])
    assert ranked[0] is c1
    assert ranked[1] is c2

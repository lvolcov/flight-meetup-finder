from datetime import datetime, time, timedelta

from app.core.filters import cheapest, filter_flights
from app.core.models import Flight, LegFilter, Stops, TimeRule


def make(hour: int, *, minute: int = 0, duration: int = 120, stops: int = 0,
         price: float = 100.0, cur: str = "GBP") -> Flight:
    dep = datetime(2026, 7, 1, hour, minute)
    return Flight(
        airline="AA",
        depart_dt=dep,
        arrive_dt=dep + timedelta(minutes=duration),
        duration_minutes=duration,
        stops=stops,
        price_amount=price,
        price_currency=cur,
    )


def test_time_preset_morning():
    rule = TimeRule.preset("morning")
    assert rule.passes(datetime(2026, 7, 1, 8, 0), datetime(2026, 7, 1, 11, 0))
    assert not rule.passes(datetime(2026, 7, 1, 5, 59), datetime(2026, 7, 1, 7, 0))
    assert not rule.passes(datetime(2026, 7, 1, 8, 0), datetime(2026, 7, 1, 12, 1))


def test_custom_time_rule():
    rule = TimeRule(depart_after=time(16, 0), arrive_before=time(22, 0))
    assert rule.passes(datetime(2026, 7, 1, 16, 0), datetime(2026, 7, 1, 22, 0))
    assert not rule.passes(datetime(2026, 7, 1, 15, 59), datetime(2026, 7, 1, 21, 0))
    assert not rule.passes(datetime(2026, 7, 1, 17, 0), datetime(2026, 7, 1, 22, 1))


def test_stops_enum():
    assert Stops.DIRECT.allows(0)
    assert not Stops.DIRECT.allows(1)
    assert Stops.ONE.allows(1)
    assert not Stops.ONE.allows(2)
    assert Stops.ANY.allows(3)


def test_leg_filter_combo():
    flights = [
        make(7, duration=180, stops=0, price=120),    # morning, direct
        make(8, duration=400, stops=1, price=80),     # too long
        make(20, duration=180, stops=0, price=90),    # evening
    ]
    lf = LegFilter(
        time_rule=TimeRule.preset("morning"),
        max_duration_minutes=240,
        max_stops=Stops.DIRECT,
    )
    out = filter_flights(flights, lf)
    assert len(out) == 1
    assert out[0].price_amount == 120


def test_cheapest():
    flights = [make(7, price=120), make(8, price=80), make(9, price=100)]
    assert cheapest(flights).price_amount == 80
    assert cheapest([]) is None

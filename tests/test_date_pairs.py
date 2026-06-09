from datetime import date

import pytest

from app.core.date_pairs import DateWindow, generate_date_pairs


def test_basic_pairs():
    out = DateWindow(date(2026, 7, 1), date(2026, 7, 3))
    ret = DateWindow(date(2026, 7, 4), date(2026, 7, 7))
    pairs = generate_date_pairs(out, ret, min_nights=3, max_nights=4)
    assert (date(2026, 7, 1), date(2026, 7, 4)) in pairs
    assert (date(2026, 7, 1), date(2026, 7, 5)) in pairs
    assert (date(2026, 7, 3), date(2026, 7, 7)) in pairs
    # nights = 2 should be excluded
    assert (date(2026, 7, 3), date(2026, 7, 5)) not in pairs


def test_weekday_filter():
    # July 2026: 2=Thu, 3=Fri, 4=Sat, 5=Sun, 6=Mon, 7=Tue, 8=Wed
    out = DateWindow(
        date(2026, 7, 1), date(2026, 7, 10),
        weekdays=frozenset({3, 4}),  # Thu, Fri only
    )
    ret = DateWindow(
        date(2026, 7, 1), date(2026, 7, 14),
        weekdays=frozenset({6, 0}),  # Sun, Mon only
    )
    pairs = generate_date_pairs(out, ret, min_nights=2, max_nights=4)
    for o, r in pairs:
        assert o.weekday() in {3, 4}
        assert r.weekday() in {6, 0}
        assert 2 <= (r - o).days <= 4


def test_no_valid_pairs():
    out = DateWindow(date(2026, 7, 1), date(2026, 7, 2))
    ret = DateWindow(date(2026, 7, 10), date(2026, 7, 11))
    assert generate_date_pairs(out, ret, 2, 4) == []


def test_pairs_sorted():
    out = DateWindow(date(2026, 7, 1), date(2026, 7, 5))
    ret = DateWindow(date(2026, 7, 4), date(2026, 7, 10))
    pairs = generate_date_pairs(out, ret, 3, 5)
    assert pairs == sorted(pairs)


def test_invalid_nights():
    out = DateWindow(date(2026, 7, 1), date(2026, 7, 2))
    ret = DateWindow(date(2026, 7, 3), date(2026, 7, 4))
    with pytest.raises(ValueError):
        generate_date_pairs(out, ret, min_nights=0, max_nights=2)
    with pytest.raises(ValueError):
        generate_date_pairs(out, ret, min_nights=5, max_nights=2)


def test_window_validation():
    with pytest.raises(ValueError):
        DateWindow(date(2026, 7, 5), date(2026, 7, 1))
    with pytest.raises(ValueError):
        DateWindow(date(2026, 7, 1), date(2026, 7, 5), weekdays=frozenset({9}))

"""fast-flights wrapper — the ONLY module allowed to import ``fast_flights``.

Purpose: turn the library's human-readable strings into the framework-free
:class:`app.core.models.Flight` dataclass, behind a swappable service
interface (N-6). Created 2026-06-09.

The library (pinned to 2.2) returns strings like ``'8:35 PM on Thu, Jul 9'``
for departure/arrival, ``'3 hr 15 min'`` for duration and ``'£99'`` / ``'€120'``
for price. We parse those here so nothing downstream sees raw scraper output.

Only ``fetch_mode="local"`` works reliably (verified 2026-06-09); the other
modes 401 / hit Google's consent page. ``get_flights`` drives Playwright via
its blocking sync API, so callers must run :meth:`FastFlightsService.search_one_way`
on a worker thread — which the async method does with ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import asdict
from datetime import date, datetime, time, timedelta
from typing import Protocol

from app.core.models import Flight

# Currency symbol -> ISO code. The app only reasons about GBP and EUR; other
# symbols are preserved so the bug is visible rather than silently coerced.
_CURRENCY_BY_SYMBOL: dict[str, str] = {"£": "GBP", "€": "EUR", "$": "USD"}

_HOURS_RE = re.compile(r"(\d+)\s*hr")
_MINS_RE = re.compile(r"(\d+)\s*min")
_TIME_RE = re.compile(r"(\d{1,2}:\d{2}\s*[AP]M)", re.IGNORECASE)
_DATE_RE = re.compile(r"([A-Za-z]{3},\s*[A-Za-z]{3}\s*\d{1,2})")
_AHEAD_RE = re.compile(r"([+-]?\d+)")


def parse_duration(text: str) -> int:
    """Parse a duration string such as ``'3 hr 15 min'`` into total minutes.

    Args:
        text: A fast-flights duration string. Accepts ``'2 hr'``, ``'45 min'``
            and the combined ``'3 hr 15 min'`` forms.

    Returns:
        Total duration in whole minutes; ``0`` if nothing parses.
    """
    hours = _HOURS_RE.search(text)
    minutes = _MINS_RE.search(text)
    total = 0
    if hours:
        total += int(hours.group(1)) * 60
    if minutes:
        total += int(minutes.group(1))
    return total


def parse_price(text: str) -> tuple[float, str]:
    """Parse a price string such as ``'£99'`` or ``'€1,234'``.

    Args:
        text: A fast-flights price string with a leading currency symbol.

    Returns:
        A ``(amount, currency_code)`` tuple. Currency defaults to ``'GBP'``
        when no known symbol is present.

    Raises:
        ValueError: If no numeric amount can be found in ``text``.
    """
    currency = "GBP"
    for symbol, code in _CURRENCY_BY_SYMBOL.items():
        if symbol in text:
            currency = code
            break
    digits = re.sub(r"[^0-9.]", "", text.replace(",", ""))
    if not digits:
        raise ValueError(f"no numeric amount in price {text!r}")
    return float(digits), currency


def parse_flight_datetime(text: str, anchor: date) -> datetime:
    """Parse a fast-flights datetime string into a concrete ``datetime``.

    The library omits the year, so ``anchor`` (the searched flight date)
    supplies it. If the parsed month/day lands well before the anchor the
    year is rolled forward by one to handle a December → January wrap.

    Args:
        text: e.g. ``'8:35 PM on Thu, Jul 9'``. If the date portion is
            missing, ``anchor`` itself is used as the date.
        anchor: The date the flight was searched for, used to resolve the
            otherwise-missing year (and as the default date).

    Returns:
        A timezone-naive ``datetime`` in the airport's local time.

    Raises:
        ValueError: If no time component can be parsed.
    """
    time_match = _TIME_RE.search(text)
    if not time_match:
        raise ValueError(f"no time component in {text!r}")
    parsed_time = datetime.strptime(
        time_match.group(1).upper().replace(" ", ""), "%I:%M%p"
    ).time()

    date_match = _DATE_RE.search(text)
    if not date_match:
        return datetime.combine(anchor, parsed_time)

    md = datetime.strptime(date_match.group(1).replace(",", ""), "%a %b %d")
    candidate = date(anchor.year, md.month, md.day)
    # December (anchor) -> January (arrival) rolls the year forward.
    if candidate < anchor - timedelta(days=2):
        candidate = date(anchor.year + 1, md.month, md.day)
    return datetime.combine(candidate, parsed_time)


def _coerce_stops(value: object) -> int:
    """Coerce the library's ``stops`` field into a non-negative int."""
    try:
        return max(0, int(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def normalise_flight(raw: object, flight_date: date) -> Flight:
    """Convert one fast-flights ``Flight`` object into our :class:`Flight`.

    Args:
        raw: A single flight object from ``fast_flights`` (duck-typed: it must
            expose ``name``, ``departure``, ``arrival``, ``duration``,
            ``stops``, ``price`` and optionally ``is_best`` /
            ``arrival_time_ahead``).
        flight_date: The searched departure date, used as the parsing anchor.

    Returns:
        A normalised, framework-free :class:`Flight`.
    """
    depart_dt = parse_flight_datetime(getattr(raw, "departure", ""), flight_date)
    arrive_dt = parse_flight_datetime(getattr(raw, "arrival", ""), flight_date)

    # Honour an explicit "+1" day-ahead hint, and guard against an arrival
    # that parsed earlier than departure (an unflagged overnight flight).
    ahead = getattr(raw, "arrival_time_ahead", "") or ""
    ahead_match = _AHEAD_RE.search(ahead)
    if ahead_match:
        days = int(ahead_match.group(1))
        if days > 0:
            arrive_dt = datetime.combine(
                depart_dt.date() + timedelta(days=days), arrive_dt.time()
            )
    if arrive_dt < depart_dt:
        arrive_dt += timedelta(days=1)

    amount, currency = parse_price(str(getattr(raw, "price", "") or ""))
    return Flight(
        airline=str(getattr(raw, "name", "") or "Unknown"),
        depart_dt=depart_dt,
        arrive_dt=arrive_dt,
        duration_minutes=parse_duration(str(getattr(raw, "duration", "") or "")),
        stops=_coerce_stops(getattr(raw, "stops", 0)),
        price_amount=amount,
        price_currency=currency,
        is_best=bool(getattr(raw, "is_best", False)),
    )


def normalise_flights(raw_flights: object, flight_date: date) -> list[Flight]:
    """Normalise a scrape result, skipping rows that cannot be parsed.

    Google sometimes returns rows with no fare (``price`` is
    ``'Price unavailable'``), which :func:`parse_price` rejects. Such a flight
    is unbookable and unrankable, so it is dropped rather than allowed to raise
    — otherwise a single price-less row would fail (and needlessly retry) the
    whole leg.

    Args:
        raw_flights: The iterable of raw flight objects from ``fast_flights``.
        flight_date: The searched departure date (parsing anchor).

    Returns:
        The parseable flights; price-less / malformed rows are skipped.
    """
    flights: list[Flight] = []
    for raw in raw_flights:  # type: ignore[union-attr]
        try:
            flights.append(normalise_flight(raw, flight_date))
        except ValueError:
            continue
    return flights


def flight_to_dict(flight: Flight) -> dict:
    """Serialise a :class:`Flight` to a JSON-safe dict (datetimes -> ISO)."""
    data = asdict(flight)
    data["depart_dt"] = flight.depart_dt.isoformat()
    data["arrive_dt"] = flight.arrive_dt.isoformat()
    return data


def flight_from_dict(data: dict) -> Flight:
    """Rebuild a :class:`Flight` from :func:`flight_to_dict` output."""
    return Flight(
        airline=data["airline"],
        depart_dt=datetime.fromisoformat(data["depart_dt"]),
        arrive_dt=datetime.fromisoformat(data["arrive_dt"]),
        duration_minutes=int(data["duration_minutes"]),
        stops=int(data["stops"]),
        price_amount=float(data["price_amount"]),
        price_currency=data["price_currency"],
        is_best=bool(data.get("is_best", False)),
    )


def google_flights_link(origin: str, destination: str, flight_date: date) -> str:
    """Build a Google Flights deep link for a one-way search.

    Used for hidden-city itineraries (F-3) where the data source cannot
    confirm the connection airport, so the user verifies manually.
    """
    query = f"Flights to {destination} from {origin} on {flight_date.isoformat()}"
    return "https://www.google.com/travel/flights?q=" + query.replace(" ", "%20")


class FlightsService(Protocol):
    """Swappable flight data source (N-6)."""

    async def search_one_way(
        self, origin: str, destination: str, flight_date: date
    ) -> list[Flight]:
        """Return normalised flight options for a single one-way search."""
        ...


class FastFlightsService:
    """Default :class:`FlightsService` backed by ``fast_flights`` (local mode)."""

    def __init__(self, fetch_mode: str = "local") -> None:
        """Store the fetch mode (only ``'local'`` is currently reliable)."""
        self._fetch_mode = fetch_mode

    def _search_blocking(
        self, origin: str, destination: str, flight_date: date
    ) -> list[Flight]:
        """Run the blocking ``get_flights`` call and normalise the result.

        Imported lazily so importing this module never drags in Playwright
        (keeps the test suite and the pure-logic layer light).
        """
        from fast_flights import FlightData, Passengers, get_flights

        result = get_flights(
            flight_data=[
                FlightData(
                    date=flight_date.isoformat(),
                    from_airport=origin,
                    to_airport=destination,
                )
            ],
            trip="one-way",
            seat="economy",
            passengers=Passengers(adults=1),
            fetch_mode=self._fetch_mode,
        )
        return normalise_flights(result.flights, flight_date)

    async def search_one_way(
        self, origin: str, destination: str, flight_date: date
    ) -> list[Flight]:
        """Search one direction, off the event loop (Playwright is blocking)."""
        return await asyncio.to_thread(
            self._search_blocking, origin, destination, flight_date
        )

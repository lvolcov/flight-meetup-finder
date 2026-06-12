"""Found-flights domain logic: de-dup signature + availability re-check.

Found flights accumulate across every search (see ``db.found_flights``). This
module owns the two pieces of logic that table needs: a stable, price-agnostic
*signature* so the same itinerary found by different searches collapses to one
row, and an *availability re-check* that re-scrapes the itinerary's routes and
reports the cheapest price now versus when it was found. Created 2026-06-12.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import date

from app.config import Settings
from app.core.filters import cheapest
from app.core.models import Flight
from app.services.flights import FlightsService

# A leg to re-scrape: (origin, destination, ISO date).
ScrapeLeg = tuple[str, str, str]


def found_signature(payload: dict) -> str:
    """Return a stable identity hash for a matched itinerary.

    The signature is deliberately **price-independent**: it covers the route,
    dates and the specific flights (airline + departure time per leg) but not
    the price, so re-finding the same itinerary at a slightly different fare
    updates the existing row instead of creating a duplicate.

    Args:
        payload: A result payload as built by the job runner (meetup or visit).

    Returns:
        A hex SHA-256 digest uniquely identifying the itinerary.
    """
    parts = [
        payload.get("kind", ""),
        payload.get("destination", ""),
        payload.get("b_origin") or "",
        payload.get("outbound_date", ""),
        payload.get("return_date", ""),
    ]
    for traveller in ("traveller_a", "traveller_b"):
        block = payload.get(traveller)
        if not block:
            continue
        for leg_key in ("outbound", "return"):
            leg = block.get(leg_key) or {}
            parts += [
                leg.get("origin", ""),
                leg.get("destination", ""),
                leg.get("airline", ""),
                leg.get("depart_dt", ""),
            ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _legs_of(payload: dict) -> list[ScrapeLeg]:
    """Return the (origin, destination, date) legs that make up an itinerary."""
    out_date = payload["outbound_date"]
    ret_date = payload["return_date"]
    legs: list[ScrapeLeg] = []
    for traveller in ("traveller_a", "traveller_b"):
        block = payload.get(traveller)
        if not block:
            continue
        outbound = block["outbound"]
        ret = block["return"]
        legs.append((outbound["origin"], outbound["destination"], out_date))
        legs.append((ret["origin"], ret["destination"], ret_date))
    return legs


def _to_gbp(flight: Flight, settings: Settings) -> float:
    """Convert a flight's price to GBP using the configured static rate."""
    if flight.price_currency == "EUR":
        return flight.price_amount * settings.eur_to_gbp
    return flight.price_amount


def _delta_note(total_now: float, saved_gbp: float) -> str:
    """Human-readable summary of the price change since the flight was found."""
    delta = total_now - saved_gbp
    now = f"Cheapest now £{total_now:.0f}"
    if abs(delta) < 0.5:
        return f"{now} — same as when you found it."
    direction = "up" if delta > 0 else "down"
    return (
        f"{now} — {direction} £{abs(delta):.0f} since you found it "
        f"(was £{saved_gbp:.0f})."
    )


async def check_availability(
    service: FlightsService,
    settings: Settings,
    payload: dict,
    saved_gbp: float,
) -> dict:
    """Re-scrape an itinerary's routes and report the cheapest price now.

    Each distinct leg is re-scraped fresh (bypassing the cache so the answer
    reflects current availability), bounded by ``scrape_concurrency`` and the
    per-scrape timeout. The cheapest option per leg is summed into a current
    combined price and compared to the price the flight was found at.

    Args:
        service: The flight data source.
        settings: App settings (concurrency, timeout, EUR→GBP rate).
        payload: The stored itinerary payload.
        saved_gbp: The combined price the itinerary was found at.

    Returns:
        ``{"status", "note", "price_gbp"}`` where ``status`` is one of
        ``"available"`` (every leg still bookable; ``price_gbp`` is the cheapest
        combined fare now), ``"gone"`` (a leg has no flights for its date now)
        or ``"error"`` (a leg could not be re-scraped).
    """
    legs = _legs_of(payload)
    distinct = list(dict.fromkeys(legs))
    semaphore = asyncio.Semaphore(max(1, settings.scrape_concurrency))
    resolved: dict[ScrapeLeg, list[Flight] | None] = {}

    async def scrape(leg: ScrapeLeg) -> None:
        origin, destination, iso = leg
        async with semaphore:
            try:
                resolved[leg] = await asyncio.wait_for(
                    service.search_one_way(
                        origin, destination, date.fromisoformat(iso)
                    ),
                    timeout=settings.scrape_timeout_seconds,
                )
            except Exception:  # noqa: BLE001 - any failure means "couldn't check"
                resolved[leg] = None

    await asyncio.gather(*(scrape(leg) for leg in distinct))

    if any(resolved[leg] is None for leg in distinct):
        return {
            "status": "error",
            "note": "Couldn't re-check right now — please try again.",
            "price_gbp": None,
        }

    total = 0.0
    for leg in legs:
        best = cheapest(resolved[leg] or [])
        if best is None:
            return {
                "status": "gone",
                "note": "No flights found on one or more legs now.",
                "price_gbp": None,
            }
        total += _to_gbp(best, settings)
    total = round(total, 2)
    return {
        "status": "available",
        "note": _delta_note(total, saved_gbp),
        "price_gbp": total,
    }

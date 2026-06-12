"""Found-flights endpoints (F-36).

Purpose: expose the persistent, de-duplicated collection of flights that
searches have turned up — list them, re-check one's current availability and
price, and remove ones the user no longer cares about. These never expire on
their own. Created 2026-06-12.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response

from app.api.deps import get_db_path, get_service, get_settings
from app.config import Settings
from app.models.schemas import FoundFlight
from app.services import db
from app.services.flights import FlightsService
from app.services.found import check_availability

router = APIRouter(prefix="/api/found-flights", tags=["found-flights"])


def _to_model(row: dict) -> FoundFlight:
    """Map a DB row to the :class:`FoundFlight` response model."""
    return FoundFlight(
        id=row["id"],
        mode=row["mode"],
        destination=row["destination"],
        b_origin=row["b_origin"],
        outbound_date=row["outbound_date"],
        return_date=row["return_date"],
        combined_gbp=row["combined_gbp"],
        payload=row["payload"],
        first_seen_at=row["first_seen_at"],
        last_seen_at=row["last_seen_at"],
        checked_at=row["checked_at"],
        check_status=row["check_status"],
        check_note=row["check_note"],
        check_price_gbp=row["check_price_gbp"],
    )


@router.get("", response_model=list[FoundFlight])
async def list_found(
    db_path: Path = Depends(get_db_path),
) -> list[FoundFlight]:
    """Return every found flight, most recently seen first."""
    return [_to_model(r) for r in await db.list_found_flights(db_path)]


@router.post("/{found_id}/check", response_model=FoundFlight)
async def check_found(
    found_id: int,
    db_path: Path = Depends(get_db_path),
    settings: Settings = Depends(get_settings),
    service: FlightsService = Depends(get_service),
) -> FoundFlight:
    """Re-scrape this itinerary's routes and report the cheapest price now."""
    row = await db.get_found_flight(db_path, found_id)
    if row is None:
        raise HTTPException(status_code=404, detail="found flight not found")
    outcome = await check_availability(
        service, settings, row["payload"], row["combined_gbp"]
    )
    await db.update_found_check(
        db_path,
        found_id,
        outcome["status"],
        outcome["note"],
        outcome["price_gbp"],
    )
    updated = await db.get_found_flight(db_path, found_id)
    assert updated is not None
    return _to_model(updated)


@router.delete("/{found_id}", status_code=204)
async def delete_found(
    found_id: int, db_path: Path = Depends(get_db_path)
) -> Response:
    """Remove a found flight from the list."""
    ok = await db.delete_found_flight(db_path, found_id)
    if not ok:
        raise HTTPException(status_code=404, detail="found flight not found")
    return Response(status_code=204)

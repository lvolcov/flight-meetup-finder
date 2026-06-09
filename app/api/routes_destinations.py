"""Destination management endpoints (F-1: editable candidate list).

Purpose: list, add, toggle and delete the candidate destinations Lucas can
search. Created 2026-06-09.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response

from app.api.deps import get_db_path
from app.models.schemas import Destination, DestinationCreate, DestinationUpdate
from app.services import db
from app.services.seed_data import KNOWN_AIRPORTS

router = APIRouter(prefix="/api/destinations", tags=["destinations"])


@router.get("", response_model=list[Destination])
async def list_destinations(
    enabled_only: bool = False, db_path: Path = Depends(get_db_path)
) -> list[Destination]:
    """Return all destinations (optionally only the enabled ones)."""
    rows = await db.list_destinations(db_path, enabled_only=enabled_only)
    return [
        Destination(iata=r["iata"], name=r["name"], enabled=bool(r["enabled"]))
        for r in rows
    ]


@router.post("", response_model=Destination, status_code=201)
async def add_destination(
    payload: DestinationCreate, db_path: Path = Depends(get_db_path)
) -> Destination:
    """Add (or re-enable) a destination by IATA code."""
    iata = payload.iata.strip().upper()
    if len(iata) != 3 or not iata.isalpha():
        raise HTTPException(status_code=422, detail="IATA code must be 3 letters")
    name = payload.name or KNOWN_AIRPORTS.get(iata, iata)
    row = await db.add_destination(db_path, iata, name)
    return Destination(iata=row["iata"], name=row["name"], enabled=True)


@router.patch("/{iata}", response_model=Destination)
async def update_destination(
    iata: str,
    payload: DestinationUpdate,
    db_path: Path = Depends(get_db_path),
) -> Destination:
    """Enable or disable a destination."""
    ok = await db.set_destination_enabled(db_path, iata, payload.enabled)
    if not ok:
        raise HTTPException(status_code=404, detail="destination not found")
    rows = await db.list_destinations(db_path)
    match = next((r for r in rows if r["iata"] == iata.strip().upper()), None)
    assert match is not None
    return Destination(
        iata=match["iata"], name=match["name"], enabled=bool(match["enabled"])
    )


@router.delete("/{iata}", status_code=204)
async def delete_destination(
    iata: str, db_path: Path = Depends(get_db_path)
) -> Response:
    """Remove a destination entirely."""
    ok = await db.delete_destination(db_path, iata)
    if not ok:
        raise HTTPException(status_code=404, detail="destination not found")
    return Response(status_code=204)

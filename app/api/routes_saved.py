"""Saved-search endpoints (F-23).

Purpose: persist named filter sets, list them with their last run, delete
them and re-run one with a single call. Created 2026-06-09.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response

from app.api.deps import get_db_path, get_runner, get_settings
from app.api.routes_jobs import create_and_enqueue
from app.config import Settings
from app.models.schemas import (
    JobCreated,
    SavedSearch,
    SavedSearchCreate,
    SearchRequest,
)
from app.services import db
from app.services.jobs import JobRunner

router = APIRouter(prefix="/api/saved-searches", tags=["saved-searches"])


def _to_model(row: dict) -> SavedSearch:
    """Map a DB row to the :class:`SavedSearch` response model."""
    return SavedSearch(
        id=row["id"],
        name=row["name"],
        mode=row["mode"],
        filters_json=row["filters_json"],
        created_at=row["created_at"],
        last_run_at=row["last_run_at"],
        last_job_id=row["last_job_id"],
    )


@router.get("", response_model=list[SavedSearch])
async def list_saved(db_path: Path = Depends(get_db_path)) -> list[SavedSearch]:
    """Return all saved searches, newest first."""
    return [_to_model(r) for r in await db.list_saved_searches(db_path)]


@router.post("", response_model=SavedSearch, status_code=201)
async def create_saved(
    payload: SavedSearchCreate, db_path: Path = Depends(get_db_path)
) -> SavedSearch:
    """Persist a named filter set."""
    search_id = await db.create_saved_search(
        db_path,
        payload.name,
        payload.request.mode,
        payload.request.model_dump_json(),
    )
    row = await db.get_saved_search(db_path, search_id)
    assert row is not None
    return _to_model(row)


@router.post("/{search_id}/run", response_model=JobCreated)
async def run_saved(
    search_id: int,
    db_path: Path = Depends(get_db_path),
    settings: Settings = Depends(get_settings),
    runner: JobRunner = Depends(get_runner),
) -> JobCreated:
    """Re-run a saved search, recording the new job against it."""
    row = await db.get_saved_search(db_path, search_id)
    if row is None:
        raise HTTPException(status_code=404, detail="saved search not found")
    request = SearchRequest.model_validate(row["filters_json"])
    created = await create_and_enqueue(request, db_path, settings, runner)
    await db.touch_saved_search(db_path, search_id, created.job_id)
    return created


@router.delete("/{search_id}", status_code=204)
async def delete_saved(
    search_id: int, db_path: Path = Depends(get_db_path)
) -> Response:
    """Delete a saved search."""
    ok = await db.delete_saved_search(db_path, search_id)
    if not ok:
        raise HTTPException(status_code=404, detail="saved search not found")
    return Response(status_code=204)

"""Search + job endpoints (F-13, F-14, F-17, F-18, F-27).

Purpose: create background searches, report progress/partial results,
estimate query counts and durations, rerun/delete old searches and save a
job's filters as a named search. Created 2026-06-09.
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response

from app.api.deps import get_db_path, get_runner, get_settings
from app.config import Settings
from app.models.schemas import (
    Estimate,
    JobCreated,
    JobStatus,
    JobSummary,
    RerunCheck,
    SaveFromJob,
    SavedSearch,
    SearchRequest,
)
from app.services import db
from app.services.jobs import JobRunner, estimate_search

router = APIRouter(prefix="/api", tags=["search"])


async def create_and_enqueue(
    request: SearchRequest,
    db_path: Path,
    settings: Settings,
    runner: JobRunner,
) -> JobCreated:
    """Estimate, persist and queue a job; return id + query/time estimate."""
    est = await estimate_search(
        db_path, request, settings.traveller_a_origin, settings
    )
    job_id = uuid.uuid4().hex
    await db.create_job(
        db_path,
        job_id,
        request.mode,
        request.model_dump_json(),
        est["estimated_queries"],
    )
    await runner.enqueue(job_id)
    return JobCreated(job_id=job_id, **est)


async def _load_job_request(db_path: Path, job_id: str) -> SearchRequest:
    """Return a job's stored filter set, or raise 404."""
    job = await db.get_job(db_path, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return SearchRequest.model_validate_json(job["filters_json"])


@router.post("/estimate", response_model=Estimate)
async def post_estimate(
    request: SearchRequest,
    db_path: Path = Depends(get_db_path),
    settings: Settings = Depends(get_settings),
) -> Estimate:
    """Estimate query count and duration without creating a job."""
    est = await estimate_search(
        db_path, request, settings.traveller_a_origin, settings
    )
    return Estimate(**est)


@router.post("/search", response_model=JobCreated)
async def post_search(
    request: SearchRequest,
    db_path: Path = Depends(get_db_path),
    settings: Settings = Depends(get_settings),
    runner: JobRunner = Depends(get_runner),
) -> JobCreated:
    """Create a background search job and return its id + estimates."""
    return await create_and_enqueue(request, db_path, settings, runner)


@router.get("/jobs", response_model=list[JobSummary])
async def list_jobs(
    limit: int = 10, db_path: Path = Depends(get_db_path)
) -> list[JobSummary]:
    """Return recent jobs so any device can find a running search again."""
    rows = await db.list_jobs(db_path, limit=min(max(limit, 1), 50))
    return [JobSummary(**row) for row in rows]


@router.get("/jobs/{job_id}", response_model=JobStatus)
async def get_job(
    job_id: str, db_path: Path = Depends(get_db_path)
) -> JobStatus:
    """Return a job's status, progress counts and partial results."""
    job = await db.get_job(db_path, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    rows = await db.list_results(db_path, job_id)
    results = [r["payload"] for r in rows if r["payload"].get("kind") != "hidden_city"]
    hidden = [r["payload"] for r in rows if r["payload"].get("kind") == "hidden_city"]
    return JobStatus(
        id=job["id"],
        mode=job["mode"],
        status=job["status"],
        queries_total=job["queries_total"],
        queries_done=job["queries_done"],
        queries_failed=job["queries_failed"],
        created_at=job["created_at"],
        error=job["error"],
        results=results,
        hidden_city=hidden,
    )


@router.post("/jobs/{job_id}/cancel", response_model=JobStatus)
async def cancel_job(
    job_id: str, db_path: Path = Depends(get_db_path)
) -> JobStatus:
    """Request cancellation of a pending/running job."""
    job = await db.get_job(db_path, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job["status"] in {"pending", "running"}:
        await db.set_job_status(db_path, job_id, "cancelled")
    return await get_job(job_id, db_path)


@router.get("/jobs/{job_id}/rerun-check", response_model=RerunCheck)
async def rerun_check(
    job_id: str,
    db_path: Path = Depends(get_db_path),
    settings: Settings = Depends(get_settings),
) -> RerunCheck:
    """Pre-flight for a rerun: are the dates still valid, and what will it cost?"""
    request = await _load_job_request(db_path, job_id)
    est = await estimate_search(
        db_path, request, settings.traveller_a_origin, settings
    )
    return RerunCheck(
        dates_in_past=request.outbound_end < date.today(),
        outbound_end=request.outbound_end.isoformat(),
        **est,
    )


@router.post("/jobs/{job_id}/rerun", response_model=JobCreated)
async def rerun_job(
    job_id: str,
    db_path: Path = Depends(get_db_path),
    settings: Settings = Depends(get_settings),
    runner: JobRunner = Depends(get_runner),
) -> JobCreated:
    """Run a fresh search with the same filters as an earlier job."""
    request = await _load_job_request(db_path, job_id)
    if request.outbound_end < date.today():
        raise HTTPException(
            status_code=409,
            detail="The dates of this search have already passed — start a "
            "new search with fresh dates.",
        )
    return await create_and_enqueue(request, db_path, settings, runner)


@router.post("/jobs/{job_id}/save", response_model=SavedSearch, status_code=201)
async def save_job_as_search(
    job_id: str,
    payload: SaveFromJob,
    db_path: Path = Depends(get_db_path),
) -> SavedSearch:
    """Save a (finished or running) job's filters as a named saved search."""
    job = await db.get_job(db_path, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    try:
        search_id = await db.create_saved_search(
            db_path, payload.name, job["mode"], job["filters_json"]
        )
    except Exception as exc:  # sqlite UNIQUE constraint on the name
        raise HTTPException(
            status_code=409, detail=f"a saved search named {payload.name!r} "
            "already exists"
        ) from exc
    row = await db.get_saved_search(db_path, search_id)
    assert row is not None
    return SavedSearch(
        id=row["id"],
        name=row["name"],
        mode=row["mode"],
        filters_json=row["filters_json"],
        created_at=row["created_at"],
        last_run_at=row["last_run_at"],
        last_job_id=row["last_job_id"],
    )


@router.delete("/jobs/{job_id}", status_code=204)
async def delete_job(
    job_id: str, db_path: Path = Depends(get_db_path)
) -> Response:
    """Delete a job and its results (a running job stops itself cleanly)."""
    ok = await db.delete_job(db_path, job_id)
    if not ok:
        raise HTTPException(status_code=404, detail="job not found")
    return Response(status_code=204)

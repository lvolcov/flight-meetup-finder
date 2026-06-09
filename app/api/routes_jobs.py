"""Search + job endpoints (F-13, F-14, F-17, F-18).

Purpose: create background searches, report progress/partial results and
cancel running jobs. Created 2026-06-09.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_db_path, get_runner, get_settings
from app.config import Settings
from app.models.schemas import JobCreated, JobStatus, JobSummary, SearchRequest
from app.services import db
from app.services.jobs import JobRunner, estimate_queries

router = APIRouter(prefix="/api", tags=["search"])


async def create_and_enqueue(
    request: SearchRequest,
    db_path: Path,
    settings: Settings,
    runner: JobRunner,
) -> JobCreated:
    """Estimate, persist and queue a job; return its id and query estimate."""
    estimate = await estimate_queries(db_path, request, settings.traveller_a_origin)
    job_id = uuid.uuid4().hex
    await db.create_job(
        db_path, job_id, request.mode, request.model_dump_json(), estimate
    )
    await runner.enqueue(job_id)
    return JobCreated(job_id=job_id, estimated_queries=estimate)


@router.post("/estimate")
async def post_estimate(
    request: SearchRequest,
    db_path: Path = Depends(get_db_path),
    settings: Settings = Depends(get_settings),
) -> dict[str, int]:
    """Return the query count a search would trigger, without creating a job."""
    estimate = await estimate_queries(db_path, request, settings.traveller_a_origin)
    return {"estimated_queries": estimate}


@router.post("/search", response_model=JobCreated)
async def post_search(
    request: SearchRequest,
    db_path: Path = Depends(get_db_path),
    settings: Settings = Depends(get_settings),
    runner: JobRunner = Depends(get_runner),
) -> JobCreated:
    """Create a background search job and return its id + query estimate."""
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

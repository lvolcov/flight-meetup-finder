"""FastAPI application factory + lifespan (ARCHITECTURE §1, N-2, N-4).

Purpose: wire the DB, job runner, routers, templates and static files into a
single-process app. The background worker is started on lifespan startup and
stopped on shutdown. Created 2026-06-09.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api import (
    routes_destinations,
    routes_jobs,
    routes_pages,
    routes_saved,
)
from app.config import get_settings
from app.services import db
from app.services.flights import FastFlightsService, FlightsService
from app.services.jobs import JobRunner

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"


def _build_service(fetch_mode: str) -> FlightsService:
    """Return the configured flight source.

    Honours ``FMF_FAKE_SCRAPER=1`` (used by e2e tests and offline demos) to
    swap in the deterministic in-process source instead of scraping Google.
    """
    if os.getenv("FMF_FAKE_SCRAPER") == "1":
        from app.services.fake import DeterministicFlightsService

        return DeterministicFlightsService()
    return FastFlightsService(fetch_mode=fetch_mode)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialise the DB and start/stop the background job runner."""
    settings = get_settings()
    db_path = settings.db_path
    await db.init_db(db_path)

    service = _build_service(settings.fetch_mode)
    runner = JobRunner(db_path, service, settings)
    await runner.start()

    app.state.settings = settings
    app.state.db_path = db_path
    app.state.service = service
    app.state.runner = runner
    try:
        yield
    finally:
        await runner.stop()


def create_app() -> FastAPI:
    """Build and return the FastAPI application."""
    app = FastAPI(title="Flight Meetup Finder", lifespan=lifespan)
    app.state.templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    app.include_router(routes_jobs.router)
    app.include_router(routes_destinations.router)
    app.include_router(routes_saved.router)
    app.include_router(routes_pages.router)

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/healthz", tags=["meta"])
    async def healthz() -> dict[str, str]:
        """Liveness probe for the container healthcheck."""
        return {"status": "ok"}

    return app


app = create_app()

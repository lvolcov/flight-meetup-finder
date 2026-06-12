"""Shared FastAPI dependencies pulling singletons off ``app.state``.

Purpose: give route handlers typed access to the settings, database path and
job runner created during the lifespan startup. Created 2026-06-09.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import Request

from app.config import Settings
from app.services.flights import FlightsService
from app.services.jobs import JobRunner


def get_settings(request: Request) -> Settings:
    """Return the app-wide :class:`Settings`."""
    return request.app.state.settings


def get_service(request: Request) -> FlightsService:
    """Return the configured :class:`FlightsService` (the flight data source)."""
    return request.app.state.service


def get_db_path(request: Request) -> Path:
    """Return the SQLite database path."""
    return request.app.state.db_path


def get_runner(request: Request) -> JobRunner:
    """Return the background :class:`JobRunner`."""
    return request.app.state.runner

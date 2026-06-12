"""Jinja2 page shells (F-20, F-21, F-23, F-24).

Purpose: render the search form, the results view and the saved-searches page.
All dynamic behaviour (polling, re-sort) happens client-side in ``app.js``;
these handlers only render the shells. Created 2026-06-09.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.api.deps import get_settings

router = APIRouter(tags=["pages"])


def _templates(request: Request):
    """Return the shared Jinja2Templates instance from app state."""
    return request.app.state.templates


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    """Render the two-tab search form."""
    settings = get_settings(request)
    return _templates(request).TemplateResponse(
        request,
        "index.html",
        {
            "traveller_a": settings.traveller_a_name,
            "traveller_b": settings.traveller_b_name,
            "a_origin": settings.traveller_a_origin,
            "b_origins": settings.traveller_b_origins,
        },
    )


@router.get("/search/{job_id}", response_class=HTMLResponse)
async def results_page(request: Request, job_id: str) -> HTMLResponse:
    """Render the live results view for a job."""
    return _templates(request).TemplateResponse(
        request, "results.html", {"job_id": job_id}
    )


@router.get("/saved", response_class=HTMLResponse)
async def saved_page(request: Request) -> HTMLResponse:
    """Render the saved-searches page."""
    return _templates(request).TemplateResponse(request, "saved.html", {})


@router.get("/found", response_class=HTMLResponse)
async def found_page(request: Request) -> HTMLResponse:
    """Render the found-flights page."""
    return _templates(request).TemplateResponse(request, "found.html", {})

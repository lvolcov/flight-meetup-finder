"""Integration tests for the FastAPI layer using the deterministic scraper.

Purpose: exercise the real endpoints (search -> poll -> results, destinations
CRUD, saved searches) end to end through ASGI, with ``FMF_FAKE_SCRAPER=1`` so
no network calls happen. Created 2026-06-09.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'fmf.db'}")
    monkeypatch.setenv("FMF_FAKE_SCRAPER", "1")
    monkeypatch.setenv("SCRAPE_DELAY_SECONDS", "0")
    from app.config import get_settings

    get_settings.cache_clear()
    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client
    get_settings.cache_clear()


def _meetup_body() -> dict:
    return {
        "mode": "meetup",
        "outbound_start": "2026-07-10",
        "outbound_end": "2026-07-10",
        "return_start": "2026-07-13",
        "return_end": "2026-07-13",
        "min_nights": 3,
        "max_nights": 3,
        "destinations": ["BCN"],
        "b_origins": ["LIS"],
    }


def _poll(client: TestClient, job_id: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["status"] in {"done", "failed", "cancelled"}:
            return body
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish: {body}")


def test_healthz(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_destinations_seeded_and_crud(client: TestClient) -> None:
    rows = client.get("/api/destinations").json()
    codes = {r["iata"] for r in rows}
    assert {"BCN", "MAD", "ATH"} <= codes

    # Disable then re-enable BCN.
    assert client.patch("/api/destinations/BCN", json={"enabled": False}).json()[
        "enabled"
    ] is False
    enabled = client.get("/api/destinations?enabled_only=true").json()
    assert "BCN" not in {r["iata"] for r in enabled}

    # Add a new airport, then delete it.
    created = client.post("/api/destinations", json={"iata": "lhr"}).json()
    assert created["iata"] == "LHR"
    assert client.delete("/api/destinations/LHR").status_code == 204
    assert client.delete("/api/destinations/LHR").status_code == 404


def test_search_flow(client: TestClient) -> None:
    created = client.post("/api/search", json=_meetup_body()).json()
    assert created["estimated_queries"] == 4
    body = _poll(client, created["job_id"])
    assert body["status"] == "done"
    assert body["queries_failed"] == 0
    assert len(body["results"]) == 1
    result = body["results"][0]
    assert result["destination"] == "BCN"
    assert result["traveller_a"]["origin"] == "MAN"
    assert result["traveller_b"]["origin"] == "LIS"
    assert "deep_link" in result["traveller_a"]["outbound"]


def test_cancel_unknown_job_404(client: TestClient) -> None:
    assert client.post("/api/jobs/nope/cancel").status_code == 404
    assert client.get("/api/jobs/nope").status_code == 404


def test_saved_search_create_and_run(client: TestClient) -> None:
    payload = {"name": "Summer BCN", "request": _meetup_body()}
    saved = client.post("/api/saved-searches", json=payload).json()
    assert saved["name"] == "Summer BCN"

    listed = client.get("/api/saved-searches").json()
    assert any(s["id"] == saved["id"] for s in listed)

    run = client.post(f"/api/saved-searches/{saved['id']}/run").json()
    body = _poll(client, run["job_id"])
    assert body["status"] == "done"

    assert client.delete(f"/api/saved-searches/{saved['id']}").status_code == 204

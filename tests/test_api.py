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

    # Schengen classification: no passport control from Lisbon for BCN/ZRH
    # (ZRH is not EU but is Schengen); EDI/GLA/DUB/TIA have immigration
    # (DUB is EU but not Schengen).
    by_iata = {r["iata"]: r for r in rows}
    for code in ("BCN", "MAD", "ZRH", "OSL", "ZAG", "OTP"):
        assert by_iata[code]["schengen"] is True, code
    for code in ("EDI", "GLA", "DUB", "TIA"):
        assert by_iata[code]["schengen"] is False, code

    # Adding a known non-Schengen airport classifies it automatically.
    created = client.post("/api/destinations", json={"iata": "LHR"}).json()
    assert created["schengen"] is False
    client.delete("/api/destinations/LHR")

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


def test_list_jobs_shows_recent_searches(client: TestClient) -> None:
    # No jobs yet.
    assert client.get("/api/jobs").json() == []

    created = client.post("/api/search", json=_meetup_body()).json()
    jobs = client.get("/api/jobs").json()
    assert len(jobs) == 1
    summary = jobs[0]
    assert summary["id"] == created["job_id"]
    assert summary["mode"] == "meetup"
    assert summary["status"] in {"pending", "running", "done"}
    assert summary["queries_total"] == 4

    # After completion the same listing reflects the final state, so a
    # different device polling /api/jobs can find and follow the search.
    _poll(client, created["job_id"])
    jobs = client.get("/api/jobs").json()
    assert jobs[0]["status"] == "done"
    assert jobs[0]["queries_done"] == 4


def test_cancel_unknown_job_404(client: TestClient) -> None:
    assert client.post("/api/jobs/nope/cancel").status_code == 404
    assert client.get("/api/jobs/nope").status_code == 404
    assert client.post("/api/jobs/nope/rerun").status_code == 404
    assert client.delete("/api/jobs/nope").status_code == 404


def test_estimate_includes_duration_and_cache_awareness(
    client: TestClient,
) -> None:
    # Cold cache: every query is uncached and costs time.
    est = client.post("/api/estimate", json=_meetup_body()).json()
    assert est["estimated_queries"] == 4
    assert est["uncached_queries"] == 4
    assert est["estimated_seconds"] > 0

    # After running the search the cache is warm, so the same estimate
    # collapses to ~zero seconds.
    created = client.post("/api/search", json=_meetup_body()).json()
    assert created["estimated_seconds"] > 0
    _poll(client, created["job_id"])
    est = client.post("/api/estimate", json=_meetup_body()).json()
    assert est["uncached_queries"] == 0
    assert est["estimated_seconds"] == 0


def test_rerun_check_and_past_dates_blocked(client: TestClient) -> None:
    # A job with valid (future) dates passes the check.
    created = client.post("/api/search", json=_meetup_body()).json()
    _poll(client, created["job_id"])
    check = client.get(f"/api/jobs/{created['job_id']}/rerun-check").json()
    assert check["dates_in_past"] is False
    assert check["estimated_queries"] == 4
    assert "estimated_seconds" in check

    # A job whose dates have passed is flagged and refused.
    old = dict(_meetup_body())
    old.update(
        outbound_start="2020-07-10", outbound_end="2020-07-10",
        return_start="2020-07-13", return_end="2020-07-13",
    )
    stale = client.post("/api/search", json=old).json()
    _poll(client, stale["job_id"])
    check = client.get(f"/api/jobs/{stale['job_id']}/rerun-check").json()
    assert check["dates_in_past"] is True
    resp = client.post(f"/api/jobs/{stale['job_id']}/rerun")
    assert resp.status_code == 409
    assert "passed" in resp.json()["detail"]


def test_save_job_as_saved_search(client: TestClient) -> None:
    created = client.post("/api/search", json=_meetup_body()).json()
    _poll(client, created["job_id"])

    saved = client.post(
        f"/api/jobs/{created['job_id']}/save", json={"name": "From results"}
    )
    assert saved.status_code == 201
    assert saved.json()["name"] == "From results"
    assert saved.json()["mode"] == "meetup"

    # Same name twice -> conflict, not a crash.
    dup = client.post(
        f"/api/jobs/{created['job_id']}/save", json={"name": "From results"}
    )
    assert dup.status_code == 409

    listed = client.get("/api/saved-searches").json()
    assert any(s["name"] == "From results" for s in listed)
    # And it runs like any saved search.
    run = client.post(f"/api/saved-searches/{saved.json()['id']}/run").json()
    assert _poll(client, run["job_id"])["status"] == "done"


def test_rerun_and_delete_job(client: TestClient) -> None:
    created = client.post("/api/search", json=_meetup_body()).json()
    _poll(client, created["job_id"])

    # Rerun creates a brand-new job with the same filters.
    rerun = client.post(f"/api/jobs/{created['job_id']}/rerun").json()
    assert rerun["job_id"] != created["job_id"]
    assert rerun["estimated_queries"] == created["estimated_queries"]
    body = _poll(client, rerun["job_id"])
    assert body["status"] == "done"
    assert len(body["results"]) == 1

    # Delete removes the job and its results.
    assert client.delete(f"/api/jobs/{created['job_id']}").status_code == 204
    assert client.get(f"/api/jobs/{created['job_id']}").status_code == 404
    assert client.delete(f"/api/jobs/{created['job_id']}").status_code == 404
    remaining = {j["id"] for j in client.get("/api/jobs").json()}
    assert created["job_id"] not in remaining
    assert rerun["job_id"] in remaining


def test_orphaned_job_resumes_on_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A job left 'running' by a restart must finish after the app reboots."""
    import asyncio
    import json

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'fmf.db'}")
    monkeypatch.setenv("FMF_FAKE_SCRAPER", "1")
    monkeypatch.setenv("SCRAPE_DELAY_SECONDS", "0")
    from app.config import get_settings

    get_settings.cache_clear()
    from app.main import create_app
    from app.services import db as dbsvc

    db_path = tmp_path / "fmf.db"

    async def seed() -> None:
        await dbsvc.init_db(db_path)
        await dbsvc.create_job(
            db_path, "orphan", "meetup", json.dumps(_meetup_body()), 4
        )
        # Simulate the state a container restart leaves behind.
        await dbsvc.set_job_status(db_path, "orphan", "running")

    asyncio.run(seed())

    with TestClient(create_app()) as test_client:
        body = _poll(test_client, "orphan")
        assert body["status"] == "done"
        assert len(body["results"]) == 1  # no duplicates from the re-run
    get_settings.cache_clear()


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


def test_found_flights_collect_check_and_delete(client: TestClient) -> None:
    # A search auto-collects its matches into the persistent Found list.
    job_id = client.post("/api/search", json=_meetup_body()).json()["job_id"]
    _poll(client, job_id)

    found = client.get("/api/found-flights").json()
    assert len(found) >= 1
    flight = found[0]
    assert flight["payload"]["kind"] == "meetup"
    assert flight["check_status"] is None  # not checked yet

    # Re-checking availability re-scrapes and records an outcome.
    checked = client.post(f"/api/found-flights/{flight['id']}/check").json()
    assert checked["check_status"] in {"available", "gone", "error"}
    assert checked["checked_at"]
    assert checked["check_note"]

    # Removal takes it off the list; it does not come back.
    assert client.delete(f"/api/found-flights/{flight['id']}").status_code == 204
    remaining = client.get("/api/found-flights").json()
    assert all(f["id"] != flight["id"] for f in remaining)

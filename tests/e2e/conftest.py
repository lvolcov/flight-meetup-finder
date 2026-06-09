"""Fixtures for the Playwright e2e suite.

Starts a real uvicorn server backed by the deterministic offline scraper
(``FMF_FAKE_SCRAPER=1``) so no Google Flights calls happen, and a headless
Chromium browser. Created 2026-06-09.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def base_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Run the app on a free port with the offline scraper; yield its URL."""
    port = _free_port()
    db = tmp_path_factory.mktemp("e2e") / "fmf.db"
    env = {
        **os.environ,
        "FMF_FAKE_SCRAPER": "1",
        "SCRAPE_DELAY_SECONDS": "0",
        "DATABASE_URL": f"sqlite:///{db}",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                urllib.request.urlopen(f"{url}/healthz", timeout=1)
                break
            except Exception:  # noqa: BLE001 - server still booting
                time.sleep(0.25)
        else:
            raise RuntimeError("server did not start")
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="session")
def browser() -> Iterator[object]:
    """Yield a headless Chromium browser, skipping if Playwright is unusable."""
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception as exc:  # noqa: BLE001 - missing browser binary
            pytest.skip(f"Chromium not available: {exc}")
        yield browser
        browser.close()


@pytest.fixture
def page(browser: object) -> Iterator[object]:
    """Yield a fresh page in its own context."""
    context = browser.new_context()  # type: ignore[attr-defined]
    pg = context.new_page()
    yield pg
    context.close()

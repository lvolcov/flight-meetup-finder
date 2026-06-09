"""SQLite persistence layer (aiosqlite).

Purpose: schema creation, seeding and all CRUD for destinations, the scrape
cache, jobs, results and saved searches (ARCHITECTURE §3). Every operation
opens a short-lived connection with WAL + foreign keys enabled, which is
plenty for a single-process, single-household app (N-1, N-4).
Created 2026-06-09.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from app.services.seed_data import SEED_DESTINATIONS

SCHEMA = """
CREATE TABLE IF NOT EXISTS destinations (
    iata        TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1,
    added_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS flight_cache (
    origin       TEXT NOT NULL,
    destination  TEXT NOT NULL,
    flight_date  TEXT NOT NULL,
    fetched_at   TEXT NOT NULL,
    payload      TEXT NOT NULL,
    PRIMARY KEY (origin, destination, flight_date)
);

CREATE TABLE IF NOT EXISTS jobs (
    id              TEXT PRIMARY KEY,
    mode            TEXT NOT NULL,
    filters_json    TEXT NOT NULL,
    status          TEXT NOT NULL,
    queries_total   INTEGER NOT NULL,
    queries_done    INTEGER NOT NULL DEFAULT 0,
    queries_failed  INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    error           TEXT
);

CREATE TABLE IF NOT EXISTS results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    destination     TEXT NOT NULL,
    b_origin        TEXT,
    outbound_date   TEXT NOT NULL,
    return_date     TEXT NOT NULL,
    payload         TEXT NOT NULL,
    combined_gbp    REAL NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS saved_searches (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL UNIQUE,
    mode          TEXT NOT NULL,
    filters_json  TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    last_run_at   TEXT,
    last_job_id   TEXT REFERENCES jobs(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_results_job ON results(job_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_cache_fetched ON flight_cache(fetched_at);
"""


def _now() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(UTC).isoformat()


def _connect(db_path: Path) -> aiosqlite.Connection:
    """Open a connection with row access by name (caller manages lifetime)."""
    conn = aiosqlite.connect(db_path)
    return conn


async def init_db(db_path: Path) -> None:
    """Create the schema (if absent) and seed destinations once.

    Args:
        db_path: Filesystem path to the SQLite file; parent dirs are created.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.executescript(SCHEMA)
        await conn.commit()
        await _seed_destinations(conn)
        await conn.commit()


async def _seed_destinations(conn: aiosqlite.Connection) -> None:
    """Insert the seed destinations, ignoring any that already exist."""
    now = _now()
    await conn.executemany(
        "INSERT OR IGNORE INTO destinations (iata, name, enabled, added_at) "
        "VALUES (?, ?, 1, ?)",
        [(iata, name, now) for iata, name in SEED_DESTINATIONS.items()],
    )


# --------------------------------------------------------------------------- #
# Destinations
# --------------------------------------------------------------------------- #
async def list_destinations(
    db_path: Path, *, enabled_only: bool = False
) -> list[dict]:
    """Return destinations ordered by name.

    Args:
        db_path: Database path.
        enabled_only: If True, return only enabled destinations.
    """
    query = "SELECT iata, name, enabled, added_at FROM destinations"
    if enabled_only:
        query += " WHERE enabled = 1"
    query += " ORDER BY name"
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        rows = await (await conn.execute(query)).fetchall()
    return [dict(row) for row in rows]


async def add_destination(db_path: Path, iata: str, name: str) -> dict:
    """Insert or update a destination by IATA code, returning the row."""
    iata = iata.strip().upper()
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "INSERT INTO destinations (iata, name, enabled, added_at) "
            "VALUES (?, ?, 1, ?) "
            "ON CONFLICT(iata) DO UPDATE SET name = excluded.name, enabled = 1",
            (iata, name, _now()),
        )
        await conn.commit()
    return {"iata": iata, "name": name, "enabled": 1}


async def set_destination_enabled(
    db_path: Path, iata: str, enabled: bool
) -> bool:
    """Toggle a destination's enabled flag. Returns False if it does not exist."""
    async with aiosqlite.connect(db_path) as conn:
        cur = await conn.execute(
            "UPDATE destinations SET enabled = ? WHERE iata = ?",
            (1 if enabled else 0, iata.strip().upper()),
        )
        await conn.commit()
        return cur.rowcount > 0


async def delete_destination(db_path: Path, iata: str) -> bool:
    """Delete a destination. Returns False if it did not exist."""
    async with aiosqlite.connect(db_path) as conn:
        cur = await conn.execute(
            "DELETE FROM destinations WHERE iata = ?", (iata.strip().upper(),)
        )
        await conn.commit()
        return cur.rowcount > 0


# --------------------------------------------------------------------------- #
# Flight cache
# --------------------------------------------------------------------------- #
async def get_cache(
    db_path: Path, origin: str, destination: str, flight_date: str
) -> dict | None:
    """Return a cached scrape row, or None if absent."""
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        row = await (
            await conn.execute(
                "SELECT origin, destination, flight_date, fetched_at, payload "
                "FROM flight_cache WHERE origin = ? AND destination = ? "
                "AND flight_date = ?",
                (origin, destination, flight_date),
            )
        ).fetchone()
    return dict(row) if row else None


async def put_cache(
    db_path: Path,
    origin: str,
    destination: str,
    flight_date: str,
    payload: list[dict],
) -> None:
    """Upsert a scrape payload with the current timestamp."""
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "INSERT INTO flight_cache (origin, destination, flight_date, "
            "fetched_at, payload) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(origin, destination, flight_date) DO UPDATE SET "
            "fetched_at = excluded.fetched_at, payload = excluded.payload",
            (origin, destination, flight_date, _now(), json.dumps(payload)),
        )
        await conn.commit()


# --------------------------------------------------------------------------- #
# Jobs
# --------------------------------------------------------------------------- #
async def create_job(
    db_path: Path, job_id: str, mode: str, filters_json: str, queries_total: int
) -> None:
    """Insert a new job row in the ``pending`` state."""
    now = _now()
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "INSERT INTO jobs (id, mode, filters_json, status, queries_total, "
            "queries_done, queries_failed, created_at, updated_at) "
            "VALUES (?, ?, ?, 'pending', ?, 0, 0, ?, ?)",
            (job_id, mode, filters_json, queries_total, now, now),
        )
        await conn.commit()


async def get_job(db_path: Path, job_id: str) -> dict | None:
    """Return a job row, or None if it does not exist."""
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        row = await (
            await conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        ).fetchone()
    return dict(row) if row else None


async def get_job_status(db_path: Path, job_id: str) -> str | None:
    """Return just a job's status (used for cancellation checks)."""
    async with aiosqlite.connect(db_path) as conn:
        row = await (
            await conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,))
        ).fetchone()
    return row[0] if row else None


async def set_job_status(
    db_path: Path, job_id: str, status: str, error: str | None = None
) -> None:
    """Set a job's status (and optional error message)."""
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "UPDATE jobs SET status = ?, error = ?, updated_at = ? WHERE id = ?",
            (status, error, _now(), job_id),
        )
        await conn.commit()


async def update_job_progress(
    db_path: Path, job_id: str, queries_done: int, queries_failed: int
) -> None:
    """Update a job's progress counters."""
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "UPDATE jobs SET queries_done = ?, queries_failed = ?, updated_at = ? "
            "WHERE id = ?",
            (queries_done, queries_failed, _now(), job_id),
        )
        await conn.commit()


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #
async def add_result(
    db_path: Path,
    job_id: str,
    destination: str,
    b_origin: str | None,
    outbound_date: str,
    return_date: str,
    payload: dict,
    combined_gbp: float,
) -> None:
    """Append one matched result row for a job."""
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "INSERT INTO results (job_id, destination, b_origin, outbound_date, "
            "return_date, payload, combined_gbp, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                job_id,
                destination,
                b_origin,
                outbound_date,
                return_date,
                json.dumps(payload),
                combined_gbp,
                _now(),
            ),
        )
        await conn.commit()


async def list_results(db_path: Path, job_id: str) -> list[dict]:
    """Return a job's matched results, cheapest combined price first."""
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        rows = await (
            await conn.execute(
                "SELECT * FROM results WHERE job_id = ? ORDER BY combined_gbp",
                (job_id,),
            )
        ).fetchall()
    out: list[dict] = []
    for row in rows:
        data = dict(row)
        data["payload"] = json.loads(data["payload"])
        out.append(data)
    return out


# --------------------------------------------------------------------------- #
# Saved searches
# --------------------------------------------------------------------------- #
async def list_saved_searches(db_path: Path) -> list[dict]:
    """Return all saved searches, newest first."""
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        rows = await (
            await conn.execute(
                "SELECT * FROM saved_searches ORDER BY created_at DESC"
            )
        ).fetchall()
    out: list[dict] = []
    for row in rows:
        data = dict(row)
        data["filters_json"] = json.loads(data["filters_json"])
        out.append(data)
    return out


async def get_saved_search(db_path: Path, search_id: int) -> dict | None:
    """Return a single saved search by id, or None."""
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        row = await (
            await conn.execute(
                "SELECT * FROM saved_searches WHERE id = ?", (search_id,)
            )
        ).fetchone()
    if not row:
        return None
    data = dict(row)
    data["filters_json"] = json.loads(data["filters_json"])
    return data


async def create_saved_search(
    db_path: Path, name: str, mode: str, filters_json: str
) -> int:
    """Insert a saved search, returning its new id."""
    async with aiosqlite.connect(db_path) as conn:
        cur = await conn.execute(
            "INSERT INTO saved_searches (name, mode, filters_json, created_at) "
            "VALUES (?, ?, ?, ?)",
            (name, mode, filters_json, _now()),
        )
        await conn.commit()
        return int(cur.lastrowid or 0)


async def touch_saved_search(
    db_path: Path, search_id: int, job_id: str
) -> None:
    """Record the latest run (timestamp + job id) for a saved search."""
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "UPDATE saved_searches SET last_run_at = ?, last_job_id = ? "
            "WHERE id = ?",
            (_now(), job_id, search_id),
        )
        await conn.commit()


async def delete_saved_search(db_path: Path, search_id: int) -> bool:
    """Delete a saved search. Returns False if it did not exist."""
    async with aiosqlite.connect(db_path) as conn:
        cur = await conn.execute(
            "DELETE FROM saved_searches WHERE id = ?", (search_id,)
        )
        await conn.commit()
        return cur.rowcount > 0

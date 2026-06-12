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

from app.services.seed_data import NON_SCHENGEN_IATA, SEED_DESTINATIONS

SCHEMA = """
CREATE TABLE IF NOT EXISTS destinations (
    iata        TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1,
    schengen    INTEGER NOT NULL DEFAULT 1,
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
    started_at      TEXT,
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

-- Found flights accumulate across every search, de-duplicated by signature,
-- and intentionally have NO foreign key to jobs: they must survive job
-- deletion, re-runs and cache expiry so the user can review them days later.
CREATE TABLE IF NOT EXISTS found_flights (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    signature        TEXT NOT NULL UNIQUE,
    mode             TEXT NOT NULL,
    destination      TEXT NOT NULL,
    b_origin         TEXT,
    outbound_date    TEXT NOT NULL,
    return_date      TEXT NOT NULL,
    combined_gbp     REAL NOT NULL,
    payload          TEXT NOT NULL,
    first_seen_at    TEXT NOT NULL,
    last_seen_at     TEXT NOT NULL,
    checked_at       TEXT,
    check_status     TEXT,
    check_note       TEXT,
    check_price_gbp  REAL
);

CREATE INDEX IF NOT EXISTS idx_results_job ON results(job_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_cache_fetched ON flight_cache(fetched_at);
CREATE INDEX IF NOT EXISTS idx_found_seen ON found_flights(last_seen_at);
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
        await _migrate(conn)
        await _seed_destinations(conn)
        await conn.commit()


async def _migrate(conn: aiosqlite.Connection) -> None:
    """Apply additive schema changes to databases created by older versions."""
    cols = [
        row[1]
        for row in await (
            await conn.execute("PRAGMA table_info(destinations)")
        ).fetchall()
    ]
    if "schengen" not in cols:
        await conn.execute(
            "ALTER TABLE destinations ADD COLUMN "
            "schengen INTEGER NOT NULL DEFAULT 1"
        )
    # started_at records when a job last entered "running" — used for an honest
    # ETA that excludes time spent queued or waiting to resume.
    job_cols = [
        row[1]
        for row in await (
            await conn.execute("PRAGMA table_info(jobs)")
        ).fetchall()
    ]
    if "started_at" not in job_cols:
        await conn.execute("ALTER TABLE jobs ADD COLUMN started_at TEXT")
    # Re-classify every boot so the known non-Schengen set stays applied to
    # rows that predate it (idempotent).
    placeholders = ",".join("?" for _ in NON_SCHENGEN_IATA)
    await conn.execute(
        f"UPDATE destinations SET schengen = 0 WHERE iata IN ({placeholders})",
        tuple(NON_SCHENGEN_IATA),
    )
    await conn.execute(
        f"UPDATE destinations SET schengen = 1 "
        f"WHERE iata NOT IN ({placeholders})",
        tuple(NON_SCHENGEN_IATA),
    )


async def _seed_destinations(conn: aiosqlite.Connection) -> None:
    """Insert the seed destinations, ignoring any that already exist."""
    now = _now()
    await conn.executemany(
        "INSERT OR IGNORE INTO destinations "
        "(iata, name, enabled, schengen, added_at) VALUES (?, ?, 1, ?, ?)",
        [
            (iata, name, 0 if iata in NON_SCHENGEN_IATA else 1, now)
            for iata, name in SEED_DESTINATIONS.items()
        ],
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
    query = "SELECT iata, name, enabled, schengen, added_at FROM destinations"
    if enabled_only:
        query += " WHERE enabled = 1"
    query += " ORDER BY name"
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        rows = await (await conn.execute(query)).fetchall()
    return [dict(row) for row in rows]


async def add_destination(
    db_path: Path, iata: str, name: str, schengen: bool = True
) -> dict:
    """Insert or update a destination by IATA code, returning the row."""
    iata = iata.strip().upper()
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "INSERT INTO destinations (iata, name, enabled, schengen, added_at) "
            "VALUES (?, ?, 1, ?, ?) "
            "ON CONFLICT(iata) DO UPDATE SET name = excluded.name, enabled = 1, "
            "schengen = excluded.schengen",
            (iata, name, 1 if schengen else 0, _now()),
        )
        await conn.commit()
    return {"iata": iata, "name": name, "enabled": 1, "schengen": schengen}


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


async def list_fresh_cache_keys(
    db_path: Path, cutoff_iso: str
) -> set[tuple[str, str, str]]:
    """Return ``(origin, destination, date)`` keys cached after ``cutoff_iso``.

    Used by the time estimator: cached queries cost ~nothing, so only the
    uncached remainder counts towards the runtime estimate.
    """
    async with aiosqlite.connect(db_path) as conn:
        rows = await (
            await conn.execute(
                "SELECT origin, destination, flight_date FROM flight_cache "
                "WHERE fetched_at > ?",
                (cutoff_iso,),
            )
        ).fetchall()
    return {(r[0], r[1], r[2]) for r in rows}


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


async def list_jobs(db_path: Path, limit: int = 10) -> list[dict]:
    """Return the most recent jobs, newest first (for the recent-searches UI)."""
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        rows = await (
            await conn.execute(
                "SELECT id, mode, status, queries_total, queries_done, "
                "queries_failed, created_at, started_at FROM jobs "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        ).fetchall()
    return [dict(row) for row in rows]


async def list_unfinished_job_ids(db_path: Path) -> list[str]:
    """Return ids of pending/running jobs, oldest first (startup recovery).

    A container restart loses the in-memory queue; these jobs would otherwise
    sit in 'running' forever. The lifespan re-enqueues them on boot.
    """
    async with aiosqlite.connect(db_path) as conn:
        rows = await (
            await conn.execute(
                "SELECT id FROM jobs WHERE status IN ('pending', 'running') "
                "ORDER BY created_at"
            )
        ).fetchall()
    return [row[0] for row in rows]


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
    """Set a job's status (and optional error message).

    Entering ``running`` (a fresh start or a resume) also stamps ``started_at``
    and resets the per-run progress counters to zero, since a resumed job
    re-evaluates from scratch — this keeps progress and the ETA consistent with
    the current run rather than a stale earlier one.
    """
    now = _now()
    async with aiosqlite.connect(db_path) as conn:
        if status == "running":
            await conn.execute(
                "UPDATE jobs SET status = ?, error = ?, updated_at = ?, "
                "started_at = ?, queries_done = 0, queries_failed = 0 "
                "WHERE id = ?",
                (status, error, now, now, job_id),
            )
        else:
            await conn.execute(
                "UPDATE jobs SET status = ?, error = ?, updated_at = ? "
                "WHERE id = ?",
                (status, error, now, job_id),
            )
        await conn.commit()


async def delete_job(db_path: Path, job_id: str) -> bool:
    """Delete a job and its results. Returns False if it did not exist.

    Results are removed explicitly because foreign-key cascade is only
    enforced on connections that enable the pragma.
    """
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("DELETE FROM results WHERE job_id = ?", (job_id,))
        cur = await conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        await conn.commit()
        return cur.rowcount > 0


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


async def clear_results(db_path: Path, job_id: str) -> None:
    """Delete a job's results (used when a resumed job re-evaluates)."""
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("DELETE FROM results WHERE job_id = ?", (job_id,))
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


# --------------------------------------------------------------------------- #
# Found flights (persistent, de-duplicated collection)
# --------------------------------------------------------------------------- #
async def upsert_found_flights(
    db_path: Path, items: list[tuple]
) -> None:
    """Insert or refresh many found flights in a single transaction.

    Each item is ``(signature, mode, destination, b_origin, outbound_date,
    return_date, payload_dict, combined_gbp)``. Re-finding an existing signature
    (matched again by a later search) refreshes its price, payload and
    ``last_seen_at`` but preserves ``first_seen_at`` — so the list never
    duplicates and always shows the latest known price. A job captures all its
    matches in one call to keep the per-match cost off the hot matching loop.
    """
    if not items:
        return
    now = _now()
    rows = [
        (
            sig,
            mode,
            destination,
            b_origin,
            outbound_date,
            return_date,
            combined_gbp,
            json.dumps(payload),
            now,
            now,
        )
        for (
            sig,
            mode,
            destination,
            b_origin,
            outbound_date,
            return_date,
            payload,
            combined_gbp,
        ) in items
    ]
    async with aiosqlite.connect(db_path) as conn:
        await conn.executemany(
            "INSERT INTO found_flights (signature, mode, destination, b_origin, "
            "outbound_date, return_date, combined_gbp, payload, first_seen_at, "
            "last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(signature) DO UPDATE SET "
            "combined_gbp = excluded.combined_gbp, payload = excluded.payload, "
            "last_seen_at = excluded.last_seen_at",
            rows,
        )
        await conn.commit()


async def list_found_flights(db_path: Path) -> list[dict]:
    """Return every found flight, most recently seen first."""
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        rows = await (
            await conn.execute(
                "SELECT * FROM found_flights ORDER BY last_seen_at DESC, id DESC"
            )
        ).fetchall()
    out: list[dict] = []
    for row in rows:
        data = dict(row)
        data["payload"] = json.loads(data["payload"])
        out.append(data)
    return out


async def get_found_flight(db_path: Path, found_id: int) -> dict | None:
    """Return a single found flight by id (payload decoded), or None."""
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        row = await (
            await conn.execute(
                "SELECT * FROM found_flights WHERE id = ?", (found_id,)
            )
        ).fetchone()
    if not row:
        return None
    data = dict(row)
    data["payload"] = json.loads(data["payload"])
    return data


async def update_found_check(
    db_path: Path,
    found_id: int,
    status: str,
    note: str,
    price_gbp: float | None,
) -> None:
    """Record the outcome of an availability re-check."""
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "UPDATE found_flights SET checked_at = ?, check_status = ?, "
            "check_note = ?, check_price_gbp = ? WHERE id = ?",
            (_now(), status, note, price_gbp, found_id),
        )
        await conn.commit()


async def delete_found_flight(db_path: Path, found_id: int) -> bool:
    """Delete a found flight. Returns False if it did not exist."""
    async with aiosqlite.connect(db_path) as conn:
        cur = await conn.execute(
            "DELETE FROM found_flights WHERE id = ?", (found_id,)
        )
        await conn.commit()
        return cur.rowcount > 0

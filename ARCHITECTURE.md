# Architecture — Flight Meetup Finder

## 1. Components

```
┌──────────────────────────────────────────────────────────────────┐
│ Browser (vanilla JS, HTML, CSS)                                  │
│   - Search form / results / saved searches                       │
│   - Polls /jobs/{id} for progress and partial results            │
└──────────────────────────────────────────────────────────────────┘
                              │ HTTP (JSON + Jinja2 templates)
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│ FastAPI app (single process)                                     │
│                                                                  │
│  ┌──────────────┐   ┌────────────────┐   ┌──────────────────┐    │
│  │ api/         │──▶│ core/          │──▶│ services/        │    │
│  │ routers      │   │ matching,      │   │ flights.py       │    │
│  │ (REST + UI)  │   │ date_pairs,    │   │ (fast-flights    │    │
│  └──────────────┘   │ filters,       │   │  wrapper)        │    │
│                     │ ranking        │   └──────────────────┘    │
│                     └────────────────┘            │              │
│                              │                    ▼              │
│                              ▼            ┌──────────────────┐   │
│                     ┌────────────────┐    │ Google Flights   │   │
│                     │ JobRunner      │    │ (scraped via     │   │
│                     │ (asyncio task) │    │  Playwright)     │   │
│                     └────────────────┘    └──────────────────┘   │
│                              │                                   │
│                              ▼                                   │
│                     ┌────────────────┐                           │
│                     │ SQLite         │ jobs, results, cache,     │
│                     │ (/data/fmf.db) │ saved_searches,           │
│                     │                │ destinations              │
│                     └────────────────┘                           │
└──────────────────────────────────────────────────────────────────┘
```

Single container, single process. The job runner is an `asyncio` task —
no Celery, no Redis. SQLite holds everything.

## 2. Data flow — meetup search

1. UI POSTs filters to `POST /api/search` → server estimates query count,
   creates a `job` row (`pending`), returns `{job_id, estimated_queries}`.
2. UI starts polling `GET /api/jobs/{id}`.
3. The job runner expands `(date pairs) × (destinations) × (B-origins) ×
   (2 directions per traveller)` into a flat list of
   `(origin, destination, date)` scrape tasks.
4. For each task: look up SQLite cache. If a row exists newer than
   `CACHE_TTL_HOURS`, use it; otherwise call `services.flights.search()`,
   sleep `SCRAPE_DELAY_SECONDS ± 30%`, store the result.
5. After each `(outbound date, return date, B-origin, destination)` tuple
   has results for both travellers in both directions, run the in-memory
   matcher (time-of-day, max duration, max stops, arrival/departure sync,
   price cap) and append surviving candidates to `results` table with the
   job id.
6. UI sees rows appear via polling; final state is `done` (or `failed` /
   `cancelled`).

Re-running the same search within TTL is fast because step 4 hits cache.
Changing filters that operate post-scrape (time-of-day, sync, caps) is
**instant** because step 5 runs against cached rows.

## 3. Database schema (SQLite)

```sql
-- Editable destination list (F-1)
CREATE TABLE destinations (
    iata        TEXT PRIMARY KEY,    -- 'BCN'
    name        TEXT NOT NULL,       -- 'Barcelona'
    enabled     INTEGER NOT NULL DEFAULT 1,
    added_at    TEXT NOT NULL
);

-- Raw scrape cache (F-19)
CREATE TABLE flight_cache (
    origin       TEXT NOT NULL,
    destination  TEXT NOT NULL,
    flight_date  TEXT NOT NULL,      -- YYYY-MM-DD
    fetched_at   TEXT NOT NULL,      -- ISO timestamp
    payload      TEXT NOT NULL,      -- JSON list of normalised flight dicts
    PRIMARY KEY (origin, destination, flight_date)
);

-- Background jobs (F-13)
CREATE TABLE jobs (
    id              TEXT PRIMARY KEY,    -- uuid
    mode            TEXT NOT NULL,       -- 'meetup' | 'visit'
    filters_json    TEXT NOT NULL,       -- serialised filter set
    status          TEXT NOT NULL,       -- pending|running|done|failed|cancelled
    queries_total   INTEGER NOT NULL,
    queries_done    INTEGER NOT NULL DEFAULT 0,
    queries_failed  INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    error           TEXT
);

-- Matched results per job
CREATE TABLE results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    destination     TEXT NOT NULL,
    b_origin        TEXT,                 -- null in visit mode
    outbound_date   TEXT NOT NULL,
    return_date     TEXT NOT NULL,
    payload         TEXT NOT NULL,        -- JSON with both itineraries
    combined_gbp    REAL NOT NULL,
    created_at      TEXT NOT NULL
);

-- Saved searches (F-23)
CREATE TABLE saved_searches (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL UNIQUE,
    mode          TEXT NOT NULL,
    filters_json  TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    last_run_at   TEXT,
    last_job_id   TEXT REFERENCES jobs(id) ON DELETE SET NULL
);

CREATE INDEX idx_results_job ON results(job_id);
CREATE INDEX idx_jobs_status ON jobs(status, updated_at);
CREATE INDEX idx_cache_fetched ON flight_cache(fetched_at);
```

## 4. The `services/flights.py` wrapper

The wrapper is the **only** module that imports `fast_flights`. It exposes
a stable interface so the data source can be swapped without touching
core / api / templates.

```python
# Pseudo-signature
class FlightOption(TypedDict):
    airline: str
    depart_dt: datetime   # parsed from fast-flights' string fields
    arrive_dt: datetime
    duration_minutes: int
    stops: int
    price_amount: float
    price_currency: str   # 'GBP' or 'EUR'
    is_best: bool
    raw: dict             # original payload for debugging
    legs: list[dict] | None  # connection airports if available

class FlightsService(Protocol):
    async def search_one_way(
        self, origin: str, destination: str, flight_date: date,
    ) -> list[FlightOption]: ...
```

The default implementation (`FastFlightsService`) wraps `get_flights(...,
fetch_mode="local")` because — verified 2026-06-09 — the other modes are
broken:

- `fetch_mode="fallback"` (the library default) → 401 from the upstream
  third-party service: `"no token provided"` / Cloudflare Turnstile.
- `fetch_mode="common"` → Google's consent page is returned instead of
  results: `RuntimeError: No flights found`.
- `fetch_mode="local"` → ✅ works. Requires Playwright + Chromium.

This is exactly why the wrapper exists: if `fast-flights` breaks entirely,
we replace `FastFlightsService` with a different scraper or paid API and
nothing else changes.

### Field mapping notes

`fast-flights` returns strings like `'8:35 PM on Thu, Jul 9'` for
departure/arrival and `'3 hr 15 min'` for duration. The wrapper is
responsible for parsing these into proper datetimes (using the search date
as the anchor) and integer minutes. Price arrives as `'£99'` / `'€120'`
— the wrapper extracts the numeric value and the currency from the
symbol.

The `legs` / connection-airports field is **not** exposed by `fast-flights`
as of 2.2 — the `Flight` object reports total `stops` but not which
airports the stops are at. For Mode 3 (hidden-city via LIS), this means
we cannot reliably detect LIS connections from the library output alone.
The wrapper therefore returns `legs=None` and the hidden-city feature
falls back to building a Google Flights deep-link for manual inspection,
as specified in F-3.

## 5. Job runner

- Implemented as an `asyncio.Task` started on app startup.
- A single in-memory `asyncio.Queue` of pending jobs.
- Per-job: one Playwright browser context reused across queries inside
  that job, closed when the job finishes (cuts startup cost).
- Cancellation: setting `jobs.status = 'cancelled'` is checked between
  queries; the worker bails out and closes the context.

## 6. Frontend

- Jinja2 templates rendered by FastAPI for the page shells.
- One global `app.js` (vanilla) handles form submission, polling and
  client-side re-sort/re-filter. No bundler.
- One `theme.css` with CSS custom properties for light/dark palettes.
- Google Fonts loaded via `<link>` — no build step.

## 7. Failure modes & honesty

- **Scraper broken**: the wrapper returns an empty list and logs the
  error; the job marks that query failed and continues. The UI shows
  `queries_failed > 0` so the user knows results are incomplete.
- **Google consent page / rate limit**: same path — retry once with
  jitter, then mark failed.
- **Library upgrade breaks signatures**: pin `fast-flights==2.2` in
  `requirements.txt` and bump deliberately.

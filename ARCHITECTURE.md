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
-- Editable destination list (F-1). schengen=0 marks airports with passport
-- control when flying from Lisbon (classified from seed_data.NON_SCHENGEN_IATA;
-- it is Schengen membership, not EU membership, that matters).
CREATE TABLE destinations (
    iata        TEXT PRIMARY KEY,    -- 'BCN'
    name        TEXT NOT NULL,       -- 'Barcelona'
    enabled     INTEGER NOT NULL DEFAULT 1,
    schengen    INTEGER NOT NULL DEFAULT 1,
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

Schema changes are applied additively at boot by `db._migrate()` (e.g. the
`schengen` column is `ALTER TABLE`-added to databases created before it
existed, and the non-Schengen classification is re-applied idempotently on
every start so the known set in `seed_data.py` stays authoritative).

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

- Implemented as an `asyncio.Task` started on app startup
  (`services/jobs.py`, wired in `main.py`'s lifespan).
- A single in-memory `asyncio.Queue` of pending job ids.
- **Restart resume**: the queue is in-memory, so on startup the lifespan
  re-enqueues every job still marked `pending`/`running` in SQLite. A
  (re)started job first clears its old `results` rows, then re-evaluates
  every tuple — already-scraped queries come from the cache, so a resume
  is cheap and results are never duplicated.
- Each query is retried once, then marked failed and skipped — one
  failure never kills the job (F-16).
- Cancellation: setting `jobs.status = 'cancelled'` is checked between
  tuples; the worker bails out.
- Browser-context reuse per job (an earlier idea in this document) is
  **not implemented**: `fast-flights` 2.2 launches its own browser inside
  every `get_flights` call and exposes no way to inject a context without
  reaching into library internals. Each scrape pays browser startup; the
  cache and throttle make this acceptable for household use.
- The scrape itself runs via `asyncio.to_thread` because `get_flights`
  drives Playwright's blocking sync API.

### Time estimation

All duration estimates share one model (`jobs.estimate_search`): expand
the request into its distinct scrape tasks, subtract the ones already in
a fresh cache (they cost ~nothing), and multiply the uncached remainder
by `SCRAPE_DELAY_SECONDS + SCRAPE_COST_SECONDS` (the latter is a rough
per-scrape wall-clock cost, configurable, default 6 s). The estimate is
shown on the search form, in the search/rerun confirmations and in saved
searches. For *running* jobs, the UI instead derives "time left" from the
job's own observed rate (`queries_done` over elapsed time since
`created_at`), shown on the results page and in the recent-searches list.

### Pricing model

Every query is a **one-way** search per `(origin, destination, date)`.
The matcher (`core/matching.py`) picks the cheapest valid option per leg
independently, so mixed-airline itineraries (e.g. Ryanair out, easyJet
back) are found naturally. The trade-off: legacy-carrier round-trip
bundles that undercut two one-ways are invisible to the tool. Each leg
carries its own `price_gbp` (EUR converted at the static `EUR_TO_GBP`
rate) in the result payload, plus a one-way Google Flights deep link.

## 6. HTTP API

```
POST   /api/estimate                 {estimated_queries, uncached_queries,
                                      estimated_seconds} — no job created
POST   /api/search                   create job -> {job_id, + estimate fields}
GET    /api/jobs?limit=N             recent jobs (recent-searches list)
GET    /api/jobs/{id}                status + counts + partial results
POST   /api/jobs/{id}/cancel         request cancellation
GET    /api/jobs/{id}/rerun-check    pre-flight: dates_in_past + fresh estimate
POST   /api/jobs/{id}/rerun          new job, same filters (409 if dates past)
POST   /api/jobs/{id}/save           save the job's filters as a saved search
DELETE /api/jobs/{id}                delete job + results (stops if running)
GET    /api/destinations[?enabled_only=true]
POST   /api/destinations             add by IATA (auto Schengen-classified)
PATCH  /api/destinations/{iata}      {enabled: bool}
DELETE /api/destinations/{iata}
GET    /api/saved-searches
POST   /api/saved-searches           {name, request}
POST   /api/saved-searches/{id}/run  re-run -> {job_id, estimated_queries}
DELETE /api/saved-searches/{id}
GET    /healthz                      container healthcheck
GET    /  /search/{job_id}  /saved   Jinja page shells
```

## 7. Frontend

- Jinja2 templates rendered by FastAPI for the page shells; all dynamic
  behaviour lives in one global `app.js` (vanilla, no bundler): form
  submission, live estimate, recent-jobs list, results polling with
  client-side re-sort/re-filter, dark mode, Schengen toggle.
- Dates: text inputs in **dd/mm/yyyy** plus a calendar button that opens
  a hidden `<input type="date">` via `showPicker()` and writes the choice
  back as dd/mm/yyyy. A bare `type="date"` is not acceptable because its
  display format follows the browser locale, not the page.
- The recent-searches list and results polling read server state, so a
  search started on the PC is visible and followable from a phone; jobs
  also survive app restarts (see §5).
- One `theme.css` with CSS custom properties for light/dark palettes;
  dark mode persists in `localStorage` and honours `prefers-color-scheme`.
- Google Fonts loaded via `<link>` — no build step.

## 8. Test strategy

- `pytest -q` (fast, offline): core logic units, wrapper string parsers,
  job-runner behaviour with fake/broken services, full ASGI integration
  via `TestClient` with `FMF_FAKE_SCRAPER=1` (incl. orphan-job resume and
  the schema migration).
- `pytest tests/e2e -o addopts=""`: Playwright drives headless Chromium
  against a real uvicorn process running the deterministic fake scraper —
  covers form rendering, dark-mode persistence, 380px mobile collapse,
  result streaming, client-side re-sort, dd/mm/yyyy entry + calendar
  sync, per-leg prices and the Schengen toggle.
- No test ever scrapes Google.

## 9. Failure modes & honesty

- **Scraper broken**: the wrapper returns an empty list and logs the
  error; the job marks that query failed and continues. The UI shows
  `queries_failed > 0` so the user knows results are incomplete.
- **Google consent page / rate limit**: same path — retry once with
  jitter, then mark failed.
- **Library upgrade breaks signatures**: pin `fast-flights==2.2` in
  `requirements.txt` and bump deliberately.

# CLAUDE.md — context for future Claude Code sessions

You are picking up the **Flight Meetup Finder** project. Read this file
first, then `REQUIREMENTS.md` and `ARCHITECTURE.md`. The user
(Lucas, `volcovlucas@gmail.com`) has private global preferences in
`~/.claude/CLAUDE.md` — those still apply (British English, PEP 8 +
type hints + Google docstrings, FastAPI, Docker, no React, conventional
commits, no over-engineering).

## What this project is

A self-hosted web tool that finds cheap, timing-compatible return
flights for two people from two different European origins to a common
destination, plus a one-way "visit Portugal" mode. Full spec is in
`REQUIREMENTS.md`.

**Status: built, deployed and in real use.** The app runs on Lucas's
Ubuntu home server (`192.168.1.107`) at port `8742`, deployed from
`/opt/flight-meetup-finder` via the server's central
`/opt/docker-compose.yml` (which also runs Pi-hole, Home Assistant,
Plex, Immich, etc.). Future work is enhancement/maintenance only.

## Stack (decided, don't second-guess)

- Python 3.12 + FastAPI, Jinja2 templates, vanilla JS/CSS frontend.
  **No React, no bundler.**
- SQLite via aiosqlite. **No external DB, no Redis, no Celery.**
- Background work via a single `asyncio.Task` in the same process. Within a
  job that one worker fans the per-leg scrapes out with bounded concurrency
  (`SCRAPE_CONCURRENCY`, default 2) via an `asyncio.Semaphore` — still one
  worker, no queue, just parallel I/O. Each scrape is capped by
  `SCRAPE_TIMEOUT_SECONDS` (default 90) so a hung browser fails soft instead
  of wedging the worker. Keep concurrency small: each scrape is a full
  headless Chromium (~300-500 MB) and Google CAPTCHAs aggressive parallelism.
- The container runs with `init: true` (tini as PID 1) so the per-scrape
  Chromium subprocesses are reaped — without it, zombie `headless_shell`
  processes accumulate until the PID table is exhausted and the worker hangs.
- `fast-flights` (pinned to **2.2**) for flight data, behind a service
  wrapper. **Must use `fetch_mode="local"`** (Playwright) — the other
  modes are broken (see `ARCHITECTURE.md` §4).
- Docker base image `mcr.microsoft.com/playwright/python:v1.49.1-noble`
  (**NOT `-jammy`** — jammy ships Python 3.10, which lacks
  `datetime.UTC`; noble ships 3.12). Host port `8742` → container `8000`.

## Conventions

- Conventional commits: `feat:`, `fix:`, `docs:`, `refactor:`, `chore:`,
  `test:`. Logical incremental commits, not one big dump. Push to
  `origin/master` when a piece of work is done and tested.
- All function signatures get type hints; Google-style docstrings.
- `pathlib` over `os.path`. f-strings over `.format`. `httpx` over
  `requests` for async.
- Pydantic v2 models for every request/response schema.
- The fast-flights interaction lives **only** in `app/services/flights.py`.
  Anything elsewhere reaching for `fast_flights` is a bug.
- The `app.core` package is framework-free — nothing in it imports
  FastAPI, Pydantic, SQLite or fast-flights. Preserve that boundary.
- Dates in the UI are **dd/mm/yyyy** (British). The API speaks ISO;
  conversion happens client-side in `app.js` (`ukToISO`/`isoToUK`).

## What's in the repo (current state)

```
app/
  main.py           # app factory + lifespan (DB init, job runner start,
                    #   re-enqueue of unfinished jobs, FMF_FAKE_SCRAPER hook)
  config.py         # pydantic-settings Settings (reads .env; NoDecode on
                    #   TRAVELLER_B_ORIGINS so "LIS,OPO,FAO" parses)
  core/             # pure logic, framework-free
    date_pairs.py   # DateWindow + generate_date_pairs
    models.py       # Flight, TimeRule, Stops, LegFilter dataclasses
    filters.py      # filter_flights, cheapest
    matching.py     # MeetupCandidate, match_meetup, gap_minutes
    ranking.py      # by_combined_price / arrival_gap / total_duration
  models/
    schemas.py      # Pydantic v2 schemas (SearchRequest, JobStatus,
                    #   JobSummary, Destination incl. schengen, SavedSearch…)
  services/
    flights.py      # ONLY fast_flights importer; parses '8:35 PM on Thu,
                    #   Jul 9' / '3 hr 15 min' / '£99' into Flight; trip is
                    #   ALWAYS one-way per (origin, dest, date) so legs are
                    #   priced individually (Ryanair out + easyJet back)
    fake.py         # DeterministicFlightsService (FMF_FAKE_SCRAPER=1) for
                    #   tests/e2e/offline demos — never hits Google
    seed_data.py    # IATA -> name seed map + NON_SCHENGEN_IATA set +
                    #   is_schengen() (passport-control classification)
    db.py           # aiosqlite schema + additive migrations (_migrate),
                    #   seed, CRUD for all five tables
    cache.py        # read-through scrape cache (TTL + jittered throttle)
    jobs.py         # asyncio JobRunner: task expansion, retry-once/fail-soft,
                    #   streams results, clears results on (re)run, estimate;
                    #   bounded-concurrency scrape phase; auto-captures matches
                    #   into found_flights (_record_found)
    found.py        # Found-flights logic: price-independent dedup signature +
                    #   availability re-check (re-scrape legs, cheapest now)
  api/
    deps.py                 # typed accessors for app.state singletons
    routes_jobs.py          # POST /api/estimate, POST /api/search,
                            #   GET /api/jobs, GET /api/jobs/{id},
                            #   cancel / rerun-check / rerun / save / DELETE
    routes_destinations.py  # GET/POST/PATCH/DELETE /api/destinations
    routes_saved.py         # GET/POST/DELETE /api/saved-searches, + /run
    routes_found.py         # GET /api/found-flights, POST {id}/check, DELETE
    routes_pages.py         # GET /, /search/{job_id}, /saved, /found (shells)
  templates/        # base, index (search form), results, saved, found,
                    #   _traveller_filters partial
  static/
    css/theme.css   # light (ivory/terracotta) + dark via CSS vars,
                    #   responsive at 380px
    js/app.js       # ALL frontend behaviour (theme, tabs, dd/mm/yyyy +
                    #   calendar button, estimate, recent jobs, polling
                    #   results, client-side re-sort/filter, Schengen toggle,
                    #   saved searches, destination manager)
tests/
  test_date_pairs.py  test_filters.py  test_matching.py    # core logic
  test_flights_parsing.py   # wrapper string parsers
  test_jobs.py              # expansion, estimate, mocked end-to-end job,
                            #   fail-soft, schema migration
  test_api.py               # full ASGI integration incl. orphan-job resume
  e2e/                      # Playwright frontend tests (real uvicorn +
    conftest.py             #   FMF_FAKE_SCRAPER=1 + headless Chromium)
    test_frontend.py
Dockerfile  docker-compose.yml  .dockerignore  pytest.ini
```

**Test counts as of 2026-06-10: 53 unit/integration + 12 e2e, all passing.**
The default `pytest -q` ignores `tests/e2e` (needs a browser); run those
with `pytest tests/e2e -o addopts=""`.

## Features beyond the original brief (all shipped)

- **Recent searches** (`GET /api/jobs` + home-page "Searches" section):
  jobs run server-side, so any device (PC or phone) can find a running
  search with live progress instead of losing it on navigation/refresh.
- **Job resume on restart**: lifespan re-enqueues jobs left
  pending/running; a resumed job clears its old result rows then
  re-evaluates (cache absorbs the re-scrapes, so it's cheap).
- **dd/mm/yyyy dates with native calendar**: text inputs + a 📅 button
  that opens a hidden `type=date` input via `showPicker()` and writes
  back dd/mm/yyyy. Plain `type=date` was rejected because its display
  format follows browser locale (showed mm/dd/yyyy).
- **Per-leg prices in GBP** on result cards (server adds `price_gbp` to
  each leg payload; EUR legs also show the original € amount).
- **Schengen classification + bulk-deselect**: `destinations.schengen`
  column, classified from `NON_SCHENGEN_IATA` in `seed_data.py` (it's
  **Schengen, not EU**, that decides passport control from Lisbon —
  Talita cannot pass non-Schengen immigration: DUB is EU but excluded;
  ZRH/OSL are non-EU but fine; RO/BG/HR are Schengen now). A "Schengen
  only" toggle on the search form deselects the passport-control
  destinations in one tap, persisted in localStorage. The DB migrates
  automatically on boot (`db._migrate`, idempotent re-classification).
- **Search management (F-33)**: rerun (with `GET /api/jobs/{id}/rerun-check`
  pre-flight — blocks past dates with 409, confirms query count + ETA),
  delete (`DELETE /api/jobs/{id}`, stops a running job cleanly — the
  runner treats a missing job row as cancellation) and save-as-named-
  search (`POST /api/jobs/{id}/save`) from the list and results pages.
- **Time estimates everywhere (F-34)**: `estimate_search` in
  `services/jobs.py` is cache-aware (fresh-cached queries cost nothing);
  uncached × (`SCRAPE_DELAY_SECONDS` + `SCRAPE_COST_SECONDS`, default 6 s).
  Returned by `/api/estimate`, `/api/search`, rerun-check; shown on the
  form, in every run confirmation, and as "time left" for running jobs
  (client-side rate from `created_at` + `queries_done`).
- **Form explanations (F-35)**: hint paragraphs + title tooltips on every
  filter, written for a non-technical user (Talita).
- **Found flights (F-36)**: a third nav tab (`/found`) holding every match
  across all searches, auto-collected and de-duplicated. Matches are captured
  automatically by the runner (`_record_found` → `db.upsert_found_flight`) into
  the `found_flights` table, which has **no FK to jobs on purpose** — these
  survive job re-runs/deletion and the cache TTL, so they never expire. Dedup
  is by a price-independent signature (`services/found.py:found_signature`:
  route + dates + per-leg airline/depart time), so re-finding refreshes the
  price + `last_seen_at` but keeps `first_seen_at`. Each card has a "Check if
  still available" button → `POST /api/found-flights/{id}/check`, which
  re-scrapes the legs fresh (`found.check_availability`, concurrency-bounded)
  and reports the cheapest combined fare now vs the saved price
  (available / gone / error), plus a Remove button (`DELETE`). Hidden-city
  deep-link "results" are intentionally **not** captured.
- **`POST /api/estimate`**: live query-count + duration estimate while
  editing the form, without creating a job.
- **SVG favicon** (`static/favicon.svg`, plane on terracotta) and a
  sliding sun/moon theme-toggle pill in the header.
- **`FMF_FAKE_SCRAPER=1`**: swaps in `DeterministicFlightsService`
  (per-destination price variation so re-sort is observable). Used by
  all e2e tests and the API integration tests.

## Pricing model (user asked about this explicitly)

Every scrape is an independent **one-way** query; the matcher picks the
cheapest valid option per leg. Mixed-airline combinations are the point.
Known limitation (be honest if it comes up): legacy-carrier round-trip
*bundles* can occasionally beat the sum of two one-ways, and the tool
cannot see those. Deep links per leg are one-way Google Flights searches.

## Verified (2026-06-09)

- `fast-flights==2.2`; `fetch_mode="local"` works (24 flights MAN→LIS).
  Field shapes: `departure` `'8:35 PM on Thu, Jul 9'`, `duration`
  `'3 hr 15 min'`, `price` `'£99'`/`'€120'`, `stops` int, `is_best` bool,
  `arrival_time_ahead` `''` or `'+1'`.
- `fetch_mode="fallback"` → 401 turnstile. `fetch_mode="common"` →
  Google consent page. Both unusable.
- fast-flights launches its own browser per `get_flights` call, so the
  ARCHITECTURE §5 idea of reusing one browser context per job is **not
  implemented** — it would require reaching into library internals.
- No real end-to-end scrape has been verified *inside the container*;
  the wrapper parsers are unit-tested against the verified string shapes.

## Deployment (home server)

- Repo cloned at `/opt/flight-meetup-finder`; `gh` CLI is authenticated
  on the server (HTTPS + credential helper), so `git pull` works.
- Service block lives in the central `/opt/docker-compose.yml` with a
  **bind mount** `/opt/flight-meetup-finder/data:/data` (owned by UID
  1001 = `pwuser`) rather than a named volume, so Duplicati's `/opt`
  backup covers the SQLite DB. The app's own `docker-compose.yml` in the
  repo is for standalone/local use and uses a named volume instead.
- Update procedure:
  ```bash
  cd /opt/flight-meetup-finder && git pull && cd /opt
  docker compose up -d --build flight-meetup-finder
  curl -fsS http://localhost:8742/healthz
  ```
  **`--build` is mandatory** — plain `up -d` reuses the stale image.
- Pi-hole on the same server is the LAN's DNS; if the server "can't
  resolve github.com", check `docker ps | grep pihole` first.
- Host port 8000 belongs to another project (instagram-tracker); this
  app must stay on 8742.

## Things the user has flagged

- Hidden-city detection: `fast-flights` does not expose connection
  airports. **Be honest in the UI** — Google Flights deep-link for
  manual verification, never fabricated connection data.
- Mobile is first-class. Test layouts at 380px width.
- Anthropic-style theme: warm ivory background, terracotta accent
  (`#CC785C` / `#DA7756`), serif headings (Lora), Inter body.
- Searches must never be "lost" by navigation, refresh or restarts —
  that's why recent-jobs + resume exist. Don't regress this.
- Talita can only fly within Schengen — keep the classification honest
  and up to date if memberships change.

## Things NOT to do

- Don't add a frontend framework. Don't add a job queue. Don't add a
  second database. Don't add auth.
- Don't claim hidden-city LIS detection works if the library doesn't
  surface connection airports — surface the limitation.
- Don't run a real scrape inside the test suite. Mock it
  (`FMF_FAKE_SCRAPER=1` or a fake service class).
- Don't commit `data/fmf.db`, `.env`, or `.venv/`.
- Don't switch the Docker base image back to `-jammy` (Python 3.10).
- Don't use `<input type="date">` directly for the date fields — its
  display format follows browser locale, which is the bug the calendar
  button + text field design fixed.

## How to run locally during development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium            # needed for e2e + real scraping
uvicorn app.main:app --reload --port 8742
# offline (no Google): FMF_FAKE_SCRAPER=1 uvicorn app.main:app --port 8742
```

## How to run tests

```bash
pytest -q                        # 49 unit + integration (offline, fast)
pytest tests/e2e -o addopts=""   # 9 Playwright e2e (offline scraper)
```

Run both after any change; do not commit red.

## GitHub

Private repo: <https://github.com/lvolcov/flight-meetup-finder>
(`origin`, branch `master`). `gh` is authenticated on both the dev
machine and the home server. Commit + push when work is done.

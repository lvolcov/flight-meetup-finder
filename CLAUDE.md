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
destination, plus a one-way "visit Portugal" mode. Runs on Lucas's home
server in Docker. Full spec is in `REQUIREMENTS.md`.

## Stack (decided, don't second-guess)

- Python 3.12 + FastAPI, Jinja2 templates, vanilla JS/CSS frontend.
  **No React, no bundler.**
- SQLite on a Docker volume. **No external DB, no Redis, no Celery.**
- Background work via `asyncio.Task` in the same process.
- `fast-flights` (pinned to **2.2**) for flight data, behind a service
  wrapper. **Must use `fetch_mode="local"`** (Playwright) — the other
  modes are broken (see `ARCHITECTURE.md` §4).
- Docker + docker-compose, single service, host port `8742` →
  container `8000`.

## Conventions

- Conventional commits: `feat:`, `fix:`, `docs:`, `refactor:`, `chore:`,
  `test:`. Logical incremental commits, not one big dump.
- All function signatures get type hints; Google-style docstrings.
- `pathlib` over `os.path`. f-strings over `.format`. `httpx` over
  `requests` for async.
- Pydantic models for every request/response schema.
- The fast-flights interaction lives **only** in `app/services/flights.py`.
  Anything elsewhere reaching for `fast_flights` is a bug.
- Tests in `tests/`, pytest with fixtures. Mock the scraper. Cover
  date-pair generation, time-window filtering, arrival/departure-gap
  matching, price ranking.

## Build order (from the original brief)

1. ✅ Docs + project skeleton (`README`, `REQUIREMENTS`, `ARCHITECTURE`,
   `CLAUDE.md`, `.env.example`, `.gitignore`).
2. ✅ Verify `fast-flights` API with a real query (see "Verified" below).
3. ✅ Core logic + unit tests (date pairs, filters, gap matching, ranking).
   17 pytest tests passing.
4. ✅ `services/flights.py` wrapper (parses fast-flights strings into `Flight`
   dataclasses), SQLite layer (`services/db.py`), read-through cache
   (`services/cache.py`) and asyncio job runner (`services/jobs.py`).
   Pydantic request/response schemas in `app/models/schemas.py`,
   settings in `app/config.py`. Tests: parsers + mocked end-to-end job.
5. ✅ FastAPI endpoints + lifespan (`app/main.py`, `app/api/*`). Search/jobs,
   destinations CRUD, saved-searches (+ run), page shells, `/healthz`.
   `FMF_FAKE_SCRAPER=1` swaps in a deterministic offline source for tests/e2e.
   Integration tests in `tests/test_api.py`.
6. ✅ Frontend: Jinja templates (`base/index/results/saved/_traveller_filters`),
   `static/css/theme.css` (light + dark, responsive at 380px),
   `static/js/app.js` (theme, tabs, live estimate, polling results with
   client-side re-sort/re-filter, saved searches, destination manager).
   Playwright e2e in `tests/e2e/` (run with `pytest tests/e2e -o addopts=""`;
   they spin up uvicorn with `FMF_FAKE_SCRAPER=1` + headless Chromium).
   5 e2e tests passing; the default `pytest -q` ignores `tests/e2e`.
7. ✅ Dockerfile + docker-compose. Base image
   `mcr.microsoft.com/playwright/python:v1.49.1-**noble**` (NOT jammy — jammy
   ships Python 3.10, which lacks `datetime.UTC`; noble ships 3.12, the
   project target). Non-root `pwuser`, `.dockerignore`, named volume
   `fmf-data` at `/data`, healthcheck on `/healthz`, host `${APP_PORT:-8742}`
   → container `8000`. Verified: `docker compose up -d --build` →
   container reports **healthy**, `/healthz` 200, 47 destinations seeded.
8. ✅ Private GitHub repo created and pushed:
   <https://github.com/lvolcov/flight-meetup-finder>.

**ALL BUILD STEPS COMPLETE.** Future work is enhancement/maintenance only.
The original brief asked for slow, incremental progress; a later session was
explicitly authorised to complete steps 4–7 in one go, which it did.

## What's in the repo (current state)

```
app/
  config.py         # pydantic-settings Settings (reads .env)
  core/
    date_pairs.py   # DateWindow + generate_date_pairs
    models.py       # Flight, TimeRule, Stops, LegFilter dataclasses
    filters.py      # filter_flights, cheapest
    matching.py     # MeetupCandidate, match_meetup, gap_minutes
    ranking.py      # by_combined_price / arrival_gap / total_duration
  models/
    schemas.py      # Pydantic v2 request/response schemas
  services/
    flights.py      # ONLY fast_flights importer; string parsers + service
    seed_data.py    # IATA -> name seed map (REQUIREMENTS §6)
    db.py           # aiosqlite schema, seed + CRUD (ARCHITECTURE §3)
    cache.py        # read-through scrape cache (F-19)
    jobs.py         # asyncio JobRunner, task expansion, query estimate
  api/ templates/ static/   # empty, awaiting steps 5-6
tests/
  test_date_pairs.py  test_filters.py  test_matching.py
  test_flights_parsing.py  test_jobs.py   (41 tests)
```

The `app.core` package is intentionally framework-free — nothing in it
imports FastAPI, Pydantic, SQLite, or fast-flights. That boundary must
be preserved when steps 4–5 are added.

## Verified (2026-06-09)

- `fast-flights==2.2` on PyPI. API:
  ```python
  from fast_flights import FlightData, Passengers, Result, get_flights
  ```
- `fetch_mode="local"` returned 24 flights for `MAN→LIS` on a date
  ~30 days out. Sample flight fields populated: `name` ('easyJet'),
  `departure` ('8:35 PM on Thu, Jul 9'), `arrival` ('11:50 PM on Thu, Jul 9'),
  `duration` ('3 hr 15 min'), `stops` (0), `price` ('£99'),
  `is_best` (True). `arrival_time_ahead` was empty string,
  `delay` was None.
- `fetch_mode="fallback"` (library default) → **broken**: 401 from
  upstream third-party turnstile validator.
- `fetch_mode="common"` (direct scrape) → **broken**: Google consent
  page returned instead of results.
- Implication: the Docker image must install Playwright Chromium
  (`playwright install chromium` at build time), and the wrapper must
  parse the human-readable date strings into datetimes itself.

## Things the user has flagged

- Hidden-city detection: `fast-flights` does not expose connection
  airports. **Be honest in the UI** about this — fall back to a Google
  Flights deep-link for manual verification rather than guessing.
- Mobile is first-class. Test layouts at 380px width.
- Anthropic-style theme: warm ivory background, terracotta accent
  (`#CC785C` / `#DA7756`), serif headings (Lora / Source Serif 4),
  Inter body.

## Things NOT to do

- Don't add a frontend framework. Don't add a job queue. Don't add a
  second database. Don't add auth.
- Don't claim hidden-city LIS detection works if the library doesn't
  surface connection airports — surface the limitation.
- Don't run a real scrape inside the test suite. Mock it.
- Don't commit `data/fmf.db`, `.env`, or `.venv/`.

## How to run locally during development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
uvicorn app.main:app --reload --port 8742
```

## How to run tests

```bash
pytest -q
```

## GitHub

The user wants this pushed to a **private** repo named
`flight-meetup-finder` via `gh repo create flight-meetup-finder
--private --source=. --push`. As of 2026-06-09 the `gh` CLI is **not
installed** on this machine — flag this to the user before attempting
the push; the local commits should still be made.

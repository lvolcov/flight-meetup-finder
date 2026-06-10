# Flight Meetup Finder

Self-hosted web tool that finds cheap, timing-compatible flights for two
people flying from two different European origins to a common destination —
plus a simpler "visit" mode for one-way meetups in Portugal.

Built for a very specific use case: Lucas lives in Manchester (MAN), Talita
lives in Lisbon (LIS / OPO / FAO). Manually cross-referencing Google Flights
from two origins to dozens of candidate destinations across a range of dates
takes hours. This tool automates it.

## Status

✅ **Deployed and in use** on the home server. Core engine, `fast-flights`
wrapper, SQLite cache, background job runner, full REST API and a responsive
Jinja/vanilla-JS UI (light + dark mode) — implemented and tested
(53 unit/integration + 12 Playwright e2e tests).

Feature highlights beyond the basics:

- **Searches are never lost** — jobs run server-side, appear in a
  "Searches" list on the home page with live progress (visible from any
  device, e.g. start on the PC, follow on the phone), and resume
  automatically if the container restarts.
- **British dates** (dd/mm/yyyy) with a native calendar picker.
- **Per-leg prices in £** alongside the combined figure (EUR legs also
  show the original € amount).
- **"Schengen only" toggle** — one tap deselects every destination with
  passport control when flying from Lisbon (UK, Ireland, Cyprus,
  Balkans…), since Talita can only travel within Schengen.
- **Leg-level pricing**: every leg is an independent one-way search, so
  mixed-airline combinations (Ryanair out, easyJet back) are found.
- **Rerun, save and delete past searches** from the list or a results
  page — a rerun checks the dates haven't passed and confirms the query
  count and expected duration first.
- **Time estimates everywhere**: how long a search will take (cache-aware)
  before you launch it, and how long is left while it runs.
- **Self-explanatory form**: plain-language hints and tooltips on every
  option, so both travellers can use it unaided.

## Quick start

```bash
cp .env.example .env
docker compose up -d --build
# open http://localhost:8742
```

The container uses the official Playwright image (`-noble`, Python 3.12) so the
Chromium browser required by the `fast-flights` local fetch mode is baked in.

## Tests

```bash
pip install -r requirements.txt
pytest -q                       # unit + API integration (offline)
playwright install chromium
pytest tests/e2e -o addopts=""  # Playwright front-end e2e (offline scraper)
```

See [`REQUIREMENTS.md`](./REQUIREMENTS.md) for the full feature spec and
[`ARCHITECTURE.md`](./ARCHITECTURE.md) for the design.

## Home-server deployment

The production instance runs from `/opt/flight-meetup-finder` via the
server's central `/opt/docker-compose.yml`, with a bind mount
`/opt/flight-meetup-finder/data:/data` (owned by UID 1001) so the SQLite
database is covered by the existing `/opt` backups. To update:

```bash
cd /opt/flight-meetup-finder && git pull && cd /opt
docker compose up -d --build flight-meetup-finder   # --build is required
curl -fsS http://localhost:8742/healthz             # {"status":"ok"}
```

## Configuration

All configuration lives in `.env`. The most important variables:

| Variable | Default | Purpose |
| -------- | ------- | ------- |
| `APP_PORT` | `8742` | Host port the web UI is exposed on. |
| `FETCH_MODE` | `local` | fast-flights fetch mode. Only `local` (Playwright) is currently reliable. |
| `SCRAPE_DELAY_SECONDS` | `1.5` | Throttle between scrape queries (±30% jitter). |
| `CACHE_TTL_HOURS` | `12` | How long a raw `(origin, destination, date)` result stays fresh. |
| `EUR_TO_GBP` | `0.85` | Static FX rate for combined-price display. |

## Caveats

- Data source is `fast-flights`, which **scrapes Google Flights**. Google can
  change its DOM at any time and break the scraper. The service layer
  isolates this so the data source can be swapped.
- Hidden-city itineraries breach airline conditions of carriage. The UI
  surfaces them with a warning; use at your own risk.

## Licence

Private project. No licence granted.

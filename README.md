# Flight Meetup Finder

Self-hosted web tool that finds cheap, timing-compatible flights for two
people flying from two different European origins to a common destination —
plus a simpler "visit" mode for one-way meetups in Portugal.

Built for a very specific use case: Lucas lives in Manchester (MAN), Talita
lives in Lisbon (LIS / OPO / FAO). Manually cross-referencing Google Flights
from two origins to dozens of candidate destinations across a range of dates
takes hours. This tool automates it.

## Status

🚧 **Work in progress.** Project skeleton, documentation and API
verification only — see commit history for what is implemented.

## Quick start

```bash
cp .env.example .env
docker compose up -d
# open http://localhost:8742
```

See [`REQUIREMENTS.md`](./REQUIREMENTS.md) for the full feature spec and
[`ARCHITECTURE.md`](./ARCHITECTURE.md) for the design.

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

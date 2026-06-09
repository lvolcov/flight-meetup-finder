# Requirements — Flight Meetup Finder

This document captures the full functional and non-functional scope of the
tool. It is the source of truth; if code disagrees with this document, one of
them is wrong.

## 1. Problem statement

Lucas (MAN) and Talita (LIS / OPO / FAO) want to meet up in Europe. Manually
cross-referencing flights from two different origins to dozens of candidate
destinations across a range of acceptable dates takes hours. This tool
automates that search and ranks viable trips by combined price.

## 2. Personas & deployment

- Single household, two travellers. **No multi-tenant, no auth.**
- Runs on Lucas's Ubuntu home server, accessed over LAN / Tailscale.
- Mobile use is a first-class concern — significant use will be from a phone.

## 3. Functional requirements

### 3.1 Search modes

- **F-1 Meetup search** — both travellers fly return to a common destination.
  - Traveller A origin: `MAN` (fixed).
  - Traveller B origins: `LIS` (default), with toggles to also include
    `OPO` and `FAO`. Each `(B-origin → destination)` is searched and the
    chosen origin is shown alongside the result.
  - Destination set: configurable candidate list, editable in the UI,
    seeded with ~30–40 popular European airports (see §6).
  - A destination is a **match** when both travellers have flights
    satisfying every active filter.
  - Results ranked by combined return price (A in GBP + B in EUR, combined
    figure shown in GBP via static FX rate).

- **F-2 Visit search** — Traveller A flies `MAN ⇄ Portugal` return.
  - Destination preferred: `LIS`. Optional toggles: `OPO`, `FAO`.
  - Single-traveller equivalent of F-1.

- **F-3 Hidden-city via Lisbon** — toggle inside F-2.
  - Additionally search `MAN → {candidate destinations}` and inspect
    itineraries whose **first leg** connects via `LIS`.
  - Surface separately, clearly labelled, with effective price and full
    itinerary.
  - If the library does not expose connection airports for a candidate
    itinerary, build the Google Flights deep-link for manual verification
    and say so honestly in the UI — do not fabricate connection data.
  - Permanent disclaimer: breaches conditions of carriage; hand luggage
    only; book one-ways; remainder of ticket is cancelled if a leg is
    skipped.

### 3.2 Filters (persisted with saved searches)

- **F-4 Outbound window**: calendar date range + allowed weekdays for the
  outbound flight.
- **F-5 Return window**: same as outbound.
- **F-6 Trip length**: min and max nights. The system generates all valid
  `(outbound date, return date)` pairs respecting F-4, F-5 and F-6.
- **F-7 Time-of-day per leg, per person** (four legs in Meetup mode, two in
  Visit mode). For each leg, either a **preset** (`Morning` 06:00–12:00,
  `Afternoon` 12:00–18:00, `Evening` 18:00–23:59, `Any`) or a **custom
  rule**: `departs after HH:MM` and/or `arrives before HH:MM`.
- **F-8 Max total journey duration** per person, hours, including
  connections. Separate values per traveller.
- **F-9 Max stops** per person: `0` (direct), `1`, or `any`.
- **F-10 Arrival sync** (Meetup only): max allowed gap, hours, between A's
  and B's arrival times at the destination.
- **F-11 Departure sync** (Meetup only): same rule for return legs leaving
  the destination.
- **F-12 Price cap**: optional max combined price (Meetup) or max price
  (Visit). GBP for A, EUR for B; combined shown in GBP using `EUR_TO_GBP`.

### 3.3 Search execution

- **F-13 Background jobs**: every search is a job with `pending /
  running / done / failed / cancelled` states. UI polls a status endpoint.
- **F-14 Progress + partial results**: report `(done, total)` query counts
  and stream partial result rows in as they become available.
- **F-15 Throttling**: configurable `SCRAPE_DELAY_SECONDS` between queries
  with random jitter (±30%).
- **F-16 Resilience**: retry each failed query once, then mark it failed
  and continue. **No single failure may kill the job.**
- **F-17 Query-count estimate**: before launching, the UI shows how many
  scrape queries the search will trigger and warns above a threshold.
- **F-18 Cancellation**: a running job can be cancelled from the UI.
- **F-19 Caching**: every `(origin, destination, date)` raw result is
  cached in SQLite with a timestamp. Within `CACHE_TTL_HOURS` the cache is
  reused. Filter changes (F-7 through F-12) are applied **after** the
  cache lookup so re-filtering never re-scrapes.

### 3.4 UI

- **F-20 Two-tab search form** (Meetup / Visit), all filters, query-count
  estimate, launch button.
- **F-21 Results view**: ranked cards (mobile) / table (desktop) with
  destination, combined price, each itinerary (airline, depart/arrive,
  duration, stops, origin used), arrival/departure-gap badges, deep links
  to the exact Google Flights search per itinerary.
- **F-22 Client-side re-sort and re-filter** of fetched results without
  re-scraping (sort by price, total duration, arrival gap).
- **F-23 Saved searches**: name a filter set, re-run with one tap, see
  history of past runs with the cheapest result of each.
- **F-24 Anthropic-inspired theme**: warm ivory background, terracotta
  accent (`#CC785C`/`#DA7756`), serif headings (Lora / Source Serif 4),
  Inter for body. Soft 12–16px corners, subtle borders.
- **F-25 Dark mode**: header toggle, persisted in `localStorage`, honours
  `prefers-color-scheme` by default.
- **F-26 Responsive**: filters collapse into an accordion on mobile, tested
  at 380px width. Results render as stacked cards on mobile.

## 4. Non-functional requirements

- **N-1 Self-contained**: SQLite file on a Docker volume; no external DB
  or message broker.
- **N-2 Containerised**: `docker compose up -d` is the only deploy step.
  Healthcheck, `restart: unless-stopped`.
- **N-3 Configurable port** (`APP_PORT`, default `8742`).
- **N-4 Single service** is acceptable (FastAPI + background worker in the
  same process via threads or `asyncio`).
- **N-5 No auth**, but the API must remain clean enough that auth could
  be added later without a rewrite.
- **N-6 Swappable data source**: the fast-flights interaction lives behind
  a service interface; replacing it must not require touching the rest of
  the app.
- **N-7 Tests** with pytest for the pure logic: date-pair generation,
  time-window filtering, arrival/departure-gap matching, price ranking.
  Scraper layer is mocked.

## 5. Out of scope

- Multi-user / authentication.
- Booking. The tool only finds and links to Google Flights.
- Hotel / accommodation search.
- Non-European destinations (no hard block; just not seeded).
- Real-time FX. Static rate via env var.

## 6. Seed candidate destinations

`BCN, MAD, FCO, CIA, MXP, BGY, LIN, VCE, NAP, BLQ, ATH, PRG, BUD, VIE,
BER, MUC, FRA, HAM, AMS, BRU, CPH, ARN, OSL, HEL, DUB, EDI, GLA, NCE,
MRS, LYS, TLS, PMI, AGP, SVQ, VLC, BIO, OTP, SOF, ZAG, SPU, DBV, TIA,
KRK, WAW, GVA, ZRH, LJU`.

Lucas can enable/disable any of these and add new airports by IATA code.

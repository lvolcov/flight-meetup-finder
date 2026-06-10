"""Background job runner — a single asyncio worker draining a job queue.

Purpose: expand a :class:`SearchRequest` into scrape tasks, resolve each leg
through the read-through cache (retry once, never let one failure kill the
job — F-16), run the in-memory matcher and stream surviving results into the
``results`` table as they are found (F-13, F-14). No Celery, no Redis: one
``asyncio.Task`` in the app process (ARCHITECTURE §5). Created 2026-06-09.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from pathlib import Path

from app.config import Settings
from app.core.filters import cheapest, filter_flights
from app.core.matching import MeetupCandidate, match_meetup
from app.core.models import Flight
from app.models.schemas import SearchRequest
from app.services import db
from app.services.cache import get_flights_cached
from app.services.flights import FlightsService, google_flights_link

logger = logging.getLogger(__name__)

# A scrape task is a distinct (origin, destination, ISO-date) triple.
ScrapeTask = tuple[str, str, str]


def _hours_to_minutes(hours: float | None) -> int | None:
    """Convert an optional hours value to whole minutes."""
    return int(hours * 60) if hours is not None else None


def expand_tasks(
    request: SearchRequest, a_origin: str, destinations: list[str]
) -> tuple[set[ScrapeTask], list[dict]]:
    """Expand a request into the distinct scrape tasks and matching tuples.

    Args:
        request: The validated search request.
        a_origin: Traveller A's fixed origin (e.g. ``'MAN'``).
        destinations: The concrete destination list to search.

    Returns:
        ``(tasks, tuples)`` where ``tasks`` is the set of distinct scrape
        triples (used both for the query estimate and resolution) and
        ``tuples`` is the ordered list of match contexts to evaluate.
    """
    from app.core.date_pairs import generate_date_pairs

    pairs = generate_date_pairs(
        request.outbound_window(),
        request.return_window(),
        request.min_nights,
        request.max_nights,
    )
    tasks: set[ScrapeTask] = set()
    tuples: list[dict] = []

    if request.mode == "meetup":
        b_origins = request.b_origins or ["LIS"]
        for dest in destinations:
            for b_origin in b_origins:
                for out_d, ret_d in pairs:
                    legs = {
                        "a_out": (a_origin, dest, out_d.isoformat()),
                        "a_ret": (dest, a_origin, ret_d.isoformat()),
                        "b_out": (b_origin, dest, out_d.isoformat()),
                        "b_ret": (dest, b_origin, ret_d.isoformat()),
                    }
                    tasks.update(legs.values())
                    tuples.append(
                        {
                            "destination": dest,
                            "b_origin": b_origin,
                            "outbound_date": out_d,
                            "return_date": ret_d,
                            "legs": legs,
                        }
                    )
    else:  # visit
        for dest in destinations:
            for out_d, ret_d in pairs:
                legs = {
                    "a_out": (a_origin, dest, out_d.isoformat()),
                    "a_ret": (dest, a_origin, ret_d.isoformat()),
                }
                tasks.update(legs.values())
                tuples.append(
                    {
                        "destination": dest,
                        "b_origin": None,
                        "outbound_date": out_d,
                        "return_date": ret_d,
                        "legs": legs,
                    }
                )
    return tasks, tuples


async def resolve_destinations(
    db_path: Path, request: SearchRequest
) -> list[str]:
    """Resolve the concrete destination list for a request.

    Meetup: the requested subset, or every enabled candidate. Visit: the
    requested Portugal targets, defaulting to ``['LIS']``.
    """
    if request.mode == "visit":
        return [d.upper() for d in (request.destinations or ["LIS"])]
    if request.destinations:
        return [d.upper() for d in request.destinations]
    rows = await db.list_destinations(db_path, enabled_only=True)
    return [row["iata"] for row in rows]


async def estimate_queries(
    db_path: Path, request: SearchRequest, a_origin: str
) -> int:
    """Return the number of distinct scrape queries a request will trigger."""
    destinations = await resolve_destinations(db_path, request)
    tasks, _ = expand_tasks(request, a_origin, destinations)
    return len(tasks)


async def estimate_search(
    db_path: Path, request: SearchRequest, a_origin: str, settings: Settings
) -> dict:
    """Estimate a search's query count and wall-clock duration.

    Cached-and-fresh queries cost ~nothing, so only the uncached remainder
    is multiplied by the per-query cost (scrape + throttle delay). The
    result therefore shrinks dramatically when re-running a recent search.

    Returns:
        ``{"estimated_queries", "uncached_queries", "estimated_seconds"}``.
    """
    from datetime import UTC, datetime, timedelta

    destinations = await resolve_destinations(db_path, request)
    tasks, _ = expand_tasks(request, a_origin, destinations)
    cutoff = (
        datetime.now(UTC) - timedelta(hours=settings.cache_ttl_hours)
    ).isoformat()
    fresh = await db.list_fresh_cache_keys(db_path, cutoff)
    uncached = sum(1 for t in tasks if t not in fresh)
    per_query = settings.scrape_delay_seconds + settings.scrape_cost_seconds
    return {
        "estimated_queries": len(tasks),
        "uncached_queries": uncached,
        "estimated_seconds": int(uncached * per_query),
    }


class JobRunner:
    """Owns the in-process job queue and its single worker task."""

    def __init__(
        self, db_path: Path, service: FlightsService, settings: Settings
    ) -> None:
        """Store dependencies; the worker is started via :meth:`start`."""
        self._db_path = db_path
        self._service = service
        self._settings = settings
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Launch the background worker (idempotent)."""
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run(), name="job-runner")

    async def stop(self) -> None:
        """Cancel the background worker and wait for it to unwind."""
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None

    async def enqueue(self, job_id: str) -> None:
        """Add a job id to the work queue."""
        await self._queue.put(job_id)

    async def _run(self) -> None:
        """Worker loop: drain the queue, isolating per-job failures."""
        while True:
            job_id = await self._queue.get()
            try:
                await self._process_job(job_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - last-resort guard
                logger.exception("job %s crashed", job_id)
                await db.set_job_status(self._db_path, job_id, "failed", str(exc))
            finally:
                self._queue.task_done()

    async def _process_job(self, job_id: str) -> None:
        """Run one job end to end, streaming results as they are matched."""
        job = await db.get_job(self._db_path, job_id)
        if job is None:
            return
        request = SearchRequest.model_validate_json(job["filters_json"])
        await db.set_job_status(self._db_path, job_id, "running")
        # A resumed job re-evaluates every tuple; drop any earlier rows so
        # results are never duplicated (re-scrapes are absorbed by the cache).
        await db.clear_results(self._db_path, job_id)

        a_origin = self._settings.traveller_a_origin
        destinations = await resolve_destinations(self._db_path, request)
        _, tuples = expand_tasks(request, a_origin, destinations)

        resolved: dict[ScrapeTask, list[Flight]] = {}
        counters = {"done": 0, "failed": 0}

        for ctx in tuples:
            # A deleted job (status None) is treated like a cancellation so
            # deleting a running search stops it cleanly mid-flight.
            status = await db.get_job_status(self._db_path, job_id)
            if status is None or status == "cancelled":
                logger.info("job %s cancelled or deleted", job_id)
                return
            await self._evaluate_tuple(job_id, request, ctx, resolved, counters)

        if request.mode == "visit" and request.hidden_city:
            await self._add_hidden_city(job_id, request, a_origin)

        status = await db.get_job_status(self._db_path, job_id)
        if status is not None and status != "cancelled":
            await db.set_job_status(self._db_path, job_id, "done")

    async def _resolve(
        self,
        job_id: str,
        task: ScrapeTask,
        resolved: dict[ScrapeTask, list[Flight]],
        counters: dict[str, int],
    ) -> list[Flight]:
        """Resolve one leg via cache, retrying once before failing soft."""
        if task in resolved:
            return resolved[task]
        origin, destination, flight_date = task
        for attempt in range(2):
            try:
                flights = await get_flights_cached(
                    self._db_path,
                    self._service,
                    self._settings,
                    origin,
                    destination,
                    flight_date,
                )
                resolved[task] = flights
                counters["done"] += 1
                break
            except Exception:  # noqa: BLE001 - retry once, then fail soft
                if attempt == 0:
                    logger.warning("retry %s after error", task)
                    continue
                logger.exception("query failed permanently: %s", task)
                resolved[task] = []
                counters["done"] += 1
                counters["failed"] += 1
        await db.update_job_progress(
            self._db_path, job_id, counters["done"], counters["failed"]
        )
        return resolved[task]

    async def _evaluate_tuple(
        self,
        job_id: str,
        request: SearchRequest,
        ctx: dict,
        resolved: dict[ScrapeTask, list[Flight]],
        counters: dict[str, int],
    ) -> None:
        """Resolve a tuple's legs, match, and persist any surviving result."""
        legs = ctx["legs"]
        leg_flights = {
            key: await self._resolve(job_id, task, resolved, counters)
            for key, task in legs.items()
        }
        if request.mode == "meetup":
            await self._match_meetup(job_id, request, ctx, leg_flights)
        else:
            await self._match_visit(job_id, request, ctx, leg_flights)

    async def _match_meetup(
        self,
        job_id: str,
        request: SearchRequest,
        ctx: dict,
        leg_flights: dict[str, list[Flight]],
    ) -> None:
        """Run the meetup matcher for one tuple and store a hit, if any."""
        traveller_b = request.traveller_b or request.traveller_a
        candidate = match_meetup(
            leg_flights["a_out"],
            leg_flights["a_ret"],
            leg_flights["b_out"],
            leg_flights["b_ret"],
            a_outbound_filter=request.traveller_a.outbound_filter(),
            a_return_filter=request.traveller_a.return_filter(),
            b_outbound_filter=traveller_b.outbound_filter(),
            b_return_filter=traveller_b.return_filter(),
            max_arrival_gap_minutes=_hours_to_minutes(request.max_arrival_gap_hours),
            max_departure_gap_minutes=_hours_to_minutes(
                request.max_departure_gap_hours
            ),
            max_combined_gbp=request.max_combined_gbp,
            eur_to_gbp=self._settings.eur_to_gbp,
        )
        if candidate is None:
            return
        payload = self._meetup_payload(ctx, candidate)
        await db.add_result(
            self._db_path,
            job_id,
            ctx["destination"],
            ctx["b_origin"],
            ctx["outbound_date"].isoformat(),
            ctx["return_date"].isoformat(),
            payload,
            candidate.combined_gbp,
        )

    async def _match_visit(
        self,
        job_id: str,
        request: SearchRequest,
        ctx: dict,
        leg_flights: dict[str, list[Flight]],
    ) -> None:
        """Pick the cheapest valid A-only return itinerary and store it."""
        out = cheapest(
            filter_flights(leg_flights["a_out"], request.traveller_a.outbound_filter())
        )
        ret = cheapest(
            filter_flights(leg_flights["a_ret"], request.traveller_a.return_filter())
        )
        if out is None or ret is None:
            return
        combined = round(
            self._to_gbp(out) + self._to_gbp(ret), 2
        )
        if request.max_price_gbp is not None and combined > request.max_price_gbp:
            return
        payload = {
            "kind": "visit",
            "destination": ctx["destination"],
            "outbound_date": ctx["outbound_date"].isoformat(),
            "return_date": ctx["return_date"].isoformat(),
            "combined_gbp": combined,
            "traveller_a": {
                "name": self._settings.traveller_a_name,
                "origin": self._settings.traveller_a_origin,
                "outbound": self._flight_dict(
                    out, self._settings.traveller_a_origin, ctx["destination"]
                ),
                "return": self._flight_dict(
                    ret, ctx["destination"], self._settings.traveller_a_origin
                ),
            },
        }
        await db.add_result(
            self._db_path,
            job_id,
            ctx["destination"],
            None,
            ctx["outbound_date"].isoformat(),
            ctx["return_date"].isoformat(),
            payload,
            combined,
        )

    async def _add_hidden_city(
        self, job_id: str, request: SearchRequest, a_origin: str
    ) -> None:
        """Store honest deep-link-only hidden-city suggestions (F-3).

        ``fast-flights`` does not expose connection airports, so we cannot
        confirm a LIS layover from the data. We therefore emit one Google
        Flights deep link per enabled candidate destination for manual
        verification, clearly labelled in the UI.
        """
        out_dates = request.outbound_window().dates()
        if not out_dates:
            return
        anchor: date = out_dates[0]
        rows = await db.list_destinations(self._db_path, enabled_only=True)
        for row in rows:
            dest = row["iata"]
            payload = {
                "kind": "hidden_city",
                "destination": dest,
                "destination_name": row["name"],
                "outbound_date": anchor.isoformat(),
                "deep_link": google_flights_link(a_origin, dest, anchor),
            }
            await db.add_result(
                self._db_path,
                job_id,
                dest,
                None,
                anchor.isoformat(),
                anchor.isoformat(),
                payload,
                0.0,
            )

    # ----------------------------------------------------------------- #
    # payload helpers
    # ----------------------------------------------------------------- #
    def _to_gbp(self, flight: Flight) -> float:
        """Convert a flight's price to GBP using the static rate."""
        if flight.price_currency == "EUR":
            return flight.price_amount * self._settings.eur_to_gbp
        return flight.price_amount

    def _flight_dict(self, flight: Flight, origin: str, destination: str) -> dict:
        """Serialise a matched leg for the result payload + deep link."""
        return {
            "airline": flight.airline,
            "origin": origin,
            "destination": destination,
            "depart_dt": flight.depart_dt.isoformat(),
            "arrive_dt": flight.arrive_dt.isoformat(),
            "duration_minutes": flight.duration_minutes,
            "stops": flight.stops,
            "price_amount": flight.price_amount,
            "price_currency": flight.price_currency,
            "price_gbp": round(self._to_gbp(flight), 2),
            "deep_link": google_flights_link(
                origin, destination, flight.depart_dt.date()
            ),
        }

    def _meetup_payload(self, ctx: dict, candidate: MeetupCandidate) -> dict:
        """Build the stored payload for a matched meetup itinerary."""
        dest = ctx["destination"]
        b_origin = ctx["b_origin"]
        a_origin = self._settings.traveller_a_origin
        return {
            "kind": "meetup",
            "destination": dest,
            "b_origin": b_origin,
            "outbound_date": ctx["outbound_date"].isoformat(),
            "return_date": ctx["return_date"].isoformat(),
            "combined_gbp": candidate.combined_gbp,
            "arrival_gap_minutes": candidate.arrival_gap_minutes,
            "departure_gap_minutes": candidate.departure_gap_minutes,
            "traveller_a": {
                "name": self._settings.traveller_a_name,
                "origin": a_origin,
                "outbound": self._flight_dict(candidate.a_outbound, a_origin, dest),
                "return": self._flight_dict(candidate.a_return, dest, a_origin),
            },
            "traveller_b": {
                "name": self._settings.traveller_b_name,
                "origin": b_origin,
                "outbound": self._flight_dict(candidate.b_outbound, b_origin, dest),
                "return": self._flight_dict(candidate.b_return, dest, b_origin),
            },
        }

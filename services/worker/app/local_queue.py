"""Postgres-backed job queue for desktop mode (QUEUE_BACKEND=postgres).

Same contract as the RabbitMQ consumer in main.py, without a broker: the API's
publish_event() commits an event_log row with status "received", and this loop
claims due rows with FOR UPDATE SKIP LOCKED, so several workers could share
the table safely. Retries reuse the same row: on failure it goes back to
"received" with available_at pushed out by the same backoff main.py uses
(min(2**retry, 30)s); after max_retries it becomes "dead_lettered", which the
Activity page's dead-letter panel already lists and can redrive.

Dedupe keys are checked the same way as main.py, including releasing the
claim on failure so a retry isn't skipped as a false duplicate.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.core.config import settings
from app.core.db import get_session
from app.core.idempotency import already_processed, clear_dedupe
from app.core.telemetry import tracer
from app.tasks import dispatch, on_dead_letter

logger = logging.getLogger(__name__)

POLL_SECONDS = 1.0
CLAIM_SQL = text(
    """
    UPDATE event_log SET status = 'processing', attempts = attempts + 1, updated_at = now()
    WHERE id IN (
        SELECT id FROM event_log
        WHERE status = 'received' AND available_at <= now()
          AND (routing_key LIKE 'rag.%' OR routing_key LIKE 'codereview.%')
        ORDER BY available_at
        FOR UPDATE SKIP LOCKED
        LIMIT :limit
    )
    RETURNING id, routing_key, dedupe_key, payload, attempts
    """
)


STALE_ERROR = "worker stopped while processing this job"

# A row whose attempts already exceed max_retries is dead-lettered instead of
# requeued: a job that kills the worker process outright (out of memory, a
# native crash) would otherwise be claimed and crash it again on every launch.
# The threshold matches _process (attempts - 1 >= max_retries).
DEAD_LETTER_STALE_SQL = text(
    "UPDATE event_log SET status = 'dead_lettered', error = :error, updated_at = now()"
    " WHERE status = 'processing' AND attempts > :max_retries"
    " RETURNING dedupe_key, routing_key, payload"
)
REQUEUE_STALE_SQL = text(
    "UPDATE event_log SET status = 'received', available_at = now(), updated_at = now()"
    " WHERE status = 'processing' RETURNING dedupe_key"
)


async def requeue_stale(session) -> int:
    """Put jobs left 'processing' by a worker that died mid-job (app quit,
    crash, restart after setup) back in the queue, and commit. Returns how
    many were requeued. Rows already past max_retries are dead-lettered
    instead (redrivable from the Activity page). Desktop runs exactly one
    worker, so at startup nothing else can legitimately hold a 'processing'
    row; with several workers sharing the table this would steal live jobs
    and would need a lease/heartbeat instead.

    Dedupe claims are released for every stale row, as _process does on
    failure, so neither the retry nor a later redrive is skipped as a false
    duplicate."""
    dead = (
        await session.execute(DEAD_LETTER_STALE_SQL, {"error": STALE_ERROR, "max_retries": settings.max_retries})
    ).all()
    requeued = (await session.execute(REQUEUE_STALE_SQL)).scalars().all()
    await session.commit()
    for dedupe_key in [*(row.dedupe_key for row in dead), *requeued]:
        if dedupe_key:
            await clear_dedupe(dedupe_key)
    if dead:
        logger.error("Dead-lettered %s job(s) the worker stopped during, past max_retries", len(dead))
    for row in dead:
        await on_dead_letter(row.routing_key, _payload(row.payload), STALE_ERROR)  # never raises
    return len(requeued)


def _payload(payload):
    """Raw-SQL JSONB comes back as text with asyncpg."""
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except ValueError:
            return None
    return payload


async def _finish(event_id, status: str, error: str | None = None, retry_in: float | None = None) -> None:
    async with get_session() as session:
        params = {"id": event_id, "status": status, "error": error}
        if retry_in is None:
            await session.execute(
                text("UPDATE event_log SET status = :status, error = :error, updated_at = now() WHERE id = :id"),
                params,
            )
        else:
            params["available_at"] = datetime.now(timezone.utc) + timedelta(seconds=retry_in)
            await session.execute(
                text(
                    "UPDATE event_log SET status = :status, error = :error, available_at = :available_at,"
                    " updated_at = now() WHERE id = :id"
                ),
                params,
            )
        await session.commit()


async def _process(row) -> None:
    event_id, routing_key, dedupe_key, payload, attempts = row
    if isinstance(payload, str):  # raw-SQL JSONB comes back as text with asyncpg
        payload = json.loads(payload)
    with tracer.start_as_current_span("process_event", attributes={"routing_key": routing_key, "dedupe_key": dedupe_key}):
        try:
            if dedupe_key and await already_processed(dedupe_key):
                logger.info("Skipping duplicate event dedupe_key=%s", dedupe_key)
                await _finish(event_id, "succeeded")
                return
            await dispatch(routing_key, payload)
            await _finish(event_id, "succeeded")
        except Exception as exc:
            logger.exception("Failed processing routing_key=%s dedupe_key=%s", routing_key, dedupe_key)
            if dedupe_key:
                await clear_dedupe(dedupe_key)
            retry_count = attempts - 1  # attempts already includes this one
            if retry_count >= settings.max_retries:
                logger.error("Dead-lettered after %s retries: %s", retry_count, dedupe_key)
                await _finish(event_id, "dead_lettered", str(exc))
                await on_dead_letter(routing_key, payload, str(exc))  # never raises
            else:
                backoff = min(2**retry_count, 30)
                logger.warning("Retrying in %ss (attempt %s/%s): %s", backoff, retry_count + 1, settings.max_retries, dedupe_key)
                await _finish(event_id, "received", str(exc), retry_in=backoff)


async def run() -> None:
    logger.info("Worker polling event_log (desktop mode, prefetch=%s)", settings.prefetch_count)
    async with get_session() as session:
        requeued = await requeue_stale(session)
    if requeued:
        logger.info("Requeued %s job(s) left in progress by a previous run", requeued)
    in_flight: set[asyncio.Task] = set()
    while True:
        free = settings.prefetch_count - len(in_flight)
        rows = []
        if free > 0:
            try:
                async with get_session() as session:
                    rows = (await session.execute(CLAIM_SQL, {"limit": free})).all()
                    await session.commit()
            except Exception:
                logger.exception("Claiming jobs failed; retrying shortly")
        for row in rows:
            task = asyncio.create_task(_process(row))
            in_flight.add(task)
            task.add_done_callback(in_flight.discard)
        if not rows:
            await asyncio.sleep(POLL_SECONDS)
        else:
            await asyncio.sleep(0)

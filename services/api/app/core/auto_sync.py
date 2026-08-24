import asyncio
import logging
from contextlib import asynccontextmanager

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.sync import sync_jira_connection, sync_notion_connection
from app.core.config import settings
from app.core.db import get_session
from app.models.rag import OAuthConnection

logger = logging.getLogger(__name__)

_task: asyncio.Task | None = None


@asynccontextmanager
async def _session_scope():
    """get_session() is written as a FastAPI dependency generator (see
    app/core/db.py) rather than an @asynccontextmanager, since every other
    caller resolves it via Depends(). This loop isn't a route handler, so it
    drives that same generator manually instead of duplicating session setup."""
    gen = get_session()
    session: AsyncSession = await anext(gen)
    try:
        yield session
    finally:
        await gen.aclose()


async def _sync_all_connections() -> None:
    async with _session_scope() as session:
        connections = (await session.execute(select(OAuthConnection))).scalars().all()

    logger.info("auto_sync: starting pass over %d connection(s)", len(connections))
    for connection in connections:
        # Each connection gets its own session/transaction — one connection's
        # failure (expired token, provider outage, rate limit exhausted past
        # sync.py's own retry budget) must not abort the pass for every other
        # connection, org-wide or otherwise.
        try:
            async with _session_scope() as session:
                # connection was loaded (and detached) by the enumeration
                # session above, not this one — merge() re-attaches it here so
                # that ensure_fresh_token()'s connection.access_token = ...;
                # await session.commit() (called from sync_jira_connection)
                # actually persists the refreshed token. Without this, the
                # mutation lands on a session-less object and session.commit()
                # silently no-ops on it: the DB keeps the stale token, and
                # every subsequent pass re-refreshes (and re-spends the same
                # refresh_token) instead of the refresh ever sticking.
                connection = await session.merge(connection)
                if connection.provider == "notion":
                    result = await sync_notion_connection(session, connection)
                elif connection.provider == "jira":
                    result = await sync_jira_connection(session, connection)
                else:
                    continue
            logger.info(
                "auto_sync: connection=%s provider=%s org=%s published=%d truncated=%s",
                connection.id,
                connection.provider,
                connection.org_id,
                result["published"],
                result["truncated"],
            )
        except Exception:
            logger.exception(
                "auto_sync: failed for connection=%s provider=%s org=%s",
                connection.id,
                connection.provider,
                connection.org_id,
            )


async def _loop() -> None:
    interval_seconds = settings.auto_sync_interval_minutes * 60
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            await _sync_all_connections()
        except asyncio.CancelledError:
            raise
        except Exception:
            # A single bad pass (e.g. a transient DB outage between passes)
            # shouldn't kill the scheduler for the rest of the process's life —
            # log and retry on the next interval.
            logger.exception("auto_sync: pass failed unexpectedly")


def start() -> None:
    """Called once from main.py's lifespan startup. No-op if disabled or
    already running (idempotent, safe to call defensively)."""
    global _task
    if not settings.auto_sync_enabled or _task is not None:
        return
    _task = asyncio.create_task(_loop())
    logger.info("auto_sync: scheduler started, interval=%dmin", settings.auto_sync_interval_minutes)


async def stop() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    _task = None

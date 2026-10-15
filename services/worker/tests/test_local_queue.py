"""Run from services/worker:  python -m pytest tests -v
Needs pgserver (desktop/requirements.txt) for a throwaway Postgres."""

import asyncio
import sys
from pathlib import Path

import pytest

WORKER_DIR = Path(__file__).resolve().parent.parent
REPO = WORKER_DIR.parent.parent
sys.path.insert(0, str(WORKER_DIR))
sys.path.insert(0, str(REPO / "desktop"))

import launcher  # noqa: E402


@pytest.fixture
def database_url(tmp_path):
    import pgserver

    server = pgserver.get_server(tmp_path / "pgdata", cleanup_mode="stop")
    try:
        uri = server.get_uri()
        asyncio.run(launcher.migrate(uri, REPO / "infra" / "postgres"))
        yield uri.replace("postgresql://", "postgresql+asyncpg://", 1)
    finally:
        server.cleanup()


@pytest.fixture
def worker_settings(database_url, monkeypatch):
    """Point the worker at the test database and an in-process fake Redis."""
    from app.core import idempotency
    from app.core.config import settings

    monkeypatch.setattr(settings, "database_url", database_url)
    monkeypatch.setattr(settings, "redis_url", "memory://")
    monkeypatch.setattr(settings, "max_retries", 3)
    monkeypatch.setattr(idempotency, "_redis", None)
    yield settings
    idempotency._redis = None


def test_stale_processing_rows_requeued(worker_settings):
    from sqlalchemy import text

    from app import local_queue
    from app.core import db

    async def scenario():
        await db.init_engine()
        try:
            async with db.get_session() as session:
                stale = (
                    await session.execute(
                        text(
                            "INSERT INTO event_log (routing_key, dedupe_key, payload, status, available_at)"
                            " VALUES ('rag.notion_page_updated', 'k1', '{}', 'processing', now() + interval '1 hour')"
                            " RETURNING id"
                        )
                    )
                ).scalar_one()
                done = (
                    await session.execute(
                        text(
                            "INSERT INTO event_log (routing_key, dedupe_key, payload, status)"
                            " VALUES ('rag.notion_page_updated', 'k2', '{}', 'succeeded') RETURNING id"
                        )
                    )
                ).scalar_one()
                await session.commit()

            async with db.get_session() as session:
                count = await local_queue.requeue_stale(session)

            async with db.get_session() as session:
                rows = dict(
                    (await session.execute(text("SELECT id, status FROM event_log WHERE id IN (:a, :b)"), {"a": stale, "b": done})).all()
                )
                due = (
                    await session.execute(text("SELECT available_at <= now() FROM event_log WHERE id = :a"), {"a": stale})
                ).scalar_one()
            return count, rows[stale], rows[done], due
        finally:
            await db.dispose_engine()

    count, stale_status, done_status, due = asyncio.run(scenario())
    assert count == 1
    assert stale_status == "received"
    assert done_status == "succeeded"
    assert due is True


def test_stale_rows_past_max_retries_dead_lettered(worker_settings):
    """A job that kills the worker on every claim must not be requeued forever."""
    from sqlalchemy import text

    from app import local_queue
    from app.core import db
    from app.core.idempotency import already_processed, get_redis

    async def scenario():
        await db.init_engine()
        try:
            async with db.get_session() as session:
                ids = {}
                # attempts 4 = claimed max_retries + 1 times; attempts 3 still has a retry left.
                for name, attempts in (("crasher", 4), ("retryable", 3)):
                    ids[name] = (
                        await session.execute(
                            text(
                                "INSERT INTO event_log (routing_key, dedupe_key, payload, status, attempts)"
                                " VALUES ('rag.notion_page_updated', :k, '{}', 'processing', :a) RETURNING id"
                            ),
                            {"k": name, "a": attempts},
                        )
                    ).scalar_one()
                await session.commit()
            # The dead worker's dedupe claims.
            await already_processed("crasher")
            await already_processed("retryable")

            async with db.get_session() as session:
                count = await local_queue.requeue_stale(session)

            async with db.get_session() as session:
                rows = {
                    r.id: r
                    for r in (
                        await session.execute(
                            text("SELECT id, status, error FROM event_log WHERE id IN (:a, :b)"),
                            {"a": ids["crasher"], "b": ids["retryable"]},
                        )
                    ).all()
                }
            claims = [await get_redis().exists(f"dedupe:{k}") for k in ("crasher", "retryable")]
            return count, rows[ids["crasher"]], rows[ids["retryable"]], claims
        finally:
            await db.dispose_engine()

    count, crasher, retryable, claims = asyncio.run(scenario())
    assert count == 1
    assert crasher.status == "dead_lettered"
    assert crasher.error == "worker stopped while processing this job"
    assert retryable.status == "received"
    assert claims == [0, 0], "stale dedupe claims are released"


def test_failing_handler_retries_with_backoff_then_dead_letters(worker_settings, monkeypatch):
    from sqlalchemy import text

    from app import local_queue
    from app.core import db
    from app.core.idempotency import get_redis

    async def boom(routing_key, payload):
        raise RuntimeError("handler exploded")

    monkeypatch.setattr(local_queue, "dispatch", boom)

    async def claim_one():
        async with db.get_session() as session:
            rows = (await session.execute(local_queue.CLAIM_SQL, {"limit": 1})).all()
            await session.commit()
        return rows

    async def state(event_id):
        async with db.get_session() as session:
            return (
                await session.execute(
                    text(
                        "SELECT status, error, attempts, available_at > now() AS future"
                        " FROM event_log WHERE id = :id"
                    ),
                    {"id": event_id},
                )
            ).one()

    async def scenario():
        await db.init_engine()
        history = []
        try:
            async with db.get_session() as session:
                event_id = (
                    await session.execute(
                        text(
                            "INSERT INTO event_log (routing_key, dedupe_key, payload, status)"
                            " VALUES ('rag.notion_page_updated', 'fail-key', '{\"page_id\": \"p\"}', 'received')"
                            " RETURNING id"
                        )
                    )
                ).scalar_one()
                await session.commit()

            for _ in range(10):
                rows = await claim_one()
                if not rows:
                    break
                await local_queue._process(rows[0])
                current = await state(event_id)
                history.append(current)
                # A retry isn't due yet: nothing else is claimable.
                if current.status == "received":
                    assert await claim_one() == []
                    async with db.get_session() as session:
                        await session.execute(
                            text("UPDATE event_log SET available_at = now() WHERE id = :id"), {"id": event_id}
                        )
                        await session.commit()
            claim = await get_redis().exists("dedupe:fail-key")
            return history, claim
        finally:
            await db.dispose_engine()

    history, claim = asyncio.run(scenario())
    # max_retries=3: attempts 1-3 are retried, attempt 4 is dead-lettered.
    assert [h.status for h in history] == ["received", "received", "received", "dead_lettered"]
    assert [h.attempts for h in history] == [1, 2, 3, 4]
    for h in history[:3]:
        assert h.future is True, "retry is scheduled in the future (backoff)"
        assert h.error == "handler exploded"
    assert history[3].error == "handler exploded"
    assert claim == 0, "failure releases the dedupe claim"

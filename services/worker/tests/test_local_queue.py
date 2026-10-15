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


def test_stale_processing_rows_requeued(database_url, monkeypatch):
    from sqlalchemy import text

    from app import local_queue
    from app.core import db
    from app.core.config import settings

    monkeypatch.setattr(settings, "database_url", database_url)

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

"""Embed events for a disconnected connection are acked and skipped.
Run from services/worker:  python -m pytest tests -v"""

import asyncio
import sys
import uuid
from pathlib import Path

import pytest

WORKER_DIR = Path(__file__).resolve().parent.parent
REPO = WORKER_DIR.parent.parent
sys.path.insert(0, str(WORKER_DIR))
sys.path.insert(0, str(REPO / "desktop"))

import launcher  # noqa: E402


@pytest.fixture
def database_url(tmp_path, monkeypatch):
    import pgserver

    from app.core.config import settings

    server = pgserver.get_server(tmp_path / "pgdata", cleanup_mode="stop")
    try:
        uri = server.get_uri()
        asyncio.run(launcher.migrate(uri, REPO / "infra" / "postgres"))
        url = uri.replace("postgresql://", "postgresql+asyncpg://", 1)
        monkeypatch.setattr(settings, "database_url", url)
        yield url
    finally:
        server.cleanup()


def _seed(provider: str, disconnected: bool, token: str | None):
    from sqlalchemy import text

    from app.core import db

    async def go():
        org, conn = uuid.uuid4(), uuid.uuid4()
        async with db.get_session() as session:
            await session.execute(
                text("INSERT INTO organizations (id, name, slug) VALUES (:id, 'T', :slug)"),
                {"id": org, "slug": f"t-{org}"},
            )
            await session.execute(
                text(
                    "INSERT INTO oauth_connections (id, org_id, provider, workspace_id, workspace_name, access_token,"
                    " disconnected_at) VALUES (:id, :org, :p, 'ws', 'W', :tok,"
                    " CASE WHEN :d THEN now() ELSE NULL END)"
                ),
                {"id": conn, "org": org, "p": provider, "tok": token, "d": disconnected},
            )
            await session.commit()
        return conn

    return go()


@pytest.mark.parametrize(
    "provider,disconnected,token",
    [("notion", True, None), ("jira", True, None), ("notion", False, None)],
)
def test_embed_skips_disconnected_connection(database_url, monkeypatch, provider, disconnected, token):
    from app.core import db
    from app.tasks import embed

    def must_not_run(*a, **k):
        raise AssertionError("a disconnected connection must not reach the provider")

    monkeypatch.setattr(embed, "NotionClient", must_not_run)
    monkeypatch.setattr(embed, "JiraClient", must_not_run)

    async def scenario():
        await db.init_engine()
        try:
            conn = await _seed(provider, disconnected, token)
            if provider == "notion":
                await embed.handle_notion_page_updated({"page_id": "p1", "connection_id": str(conn)})
            else:
                await embed.handle_jira_issue_updated({"issue_key": "K-1", "issue_id": "1", "connection_id": str(conn)})
        finally:
            await db.dispose_engine()

    asyncio.run(scenario())  # returns normally: acked, not retried or dead-lettered

"""Disconnecting a Notion/Jira connection: tokens wiped, syncing stopped,
documents removed or kept, and reconnecting reuses the row."""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from app.api.routes import connections as connections_routes
from app.api.routes import documents as documents_routes
from app.api.routes import sync as sync_routes
from app.core import auto_sync
from app.core import ratelimit
from app.core.auth import AuthContext, require_auth_or_service_token
from app.core.crypto import encrypt_token
from app.integrations import connections as saving
from app.integrations import jira_auth

from .conftest import db_run

ROUTERS = [
    (connections_routes.router, "/connections"),
    (sync_routes.router, "/sync"),
    (documents_routes.router, "/documents"),
]


@pytest.fixture
def client_for(make_client, monkeypatch):
    from fakeredis import FakeAsyncRedis

    fake = FakeAsyncRedis(decode_responses=True)
    monkeypatch.setattr(ratelimit, "get_redis", lambda: fake)

    def _make(role="owner", mode="desktop"):
        client = make_client(db=True, role=role, mode=mode, routers=ROUTERS)
        client.app.dependency_overrides[require_auth_or_service_token] = lambda: AuthContext(
            org_id=client.org_id, role=role, user_id=client.user_id
        )
        return client

    return _make


def seed(dsn, org_id, provider="notion", workspace_id="ws-1", docs=2):
    """A connected row with `docs` documents (one chunk each). Returns the connection id."""
    conn_id = uuid.uuid4()
    db_run(
        dsn,
        "INSERT INTO oauth_connections (id, org_id, provider, workspace_id, workspace_name, access_token,"
        " refresh_token, expires_at) VALUES ($1, $2, $3, $4, 'Acme', $5, $6, $7)",
        conn_id,
        org_id,
        provider,
        workspace_id,
        encrypt_token("live-access"),
        encrypt_token("live-refresh") if provider == "jira" else None,
        datetime.now(timezone.utc) + timedelta(hours=1) if provider == "jira" else None,
    )
    for i in range(docs):
        doc_id = uuid.uuid4()
        db_run(
            dsn,
            "INSERT INTO documents (id, source, external_id, connection_id, title, url)"
            " VALUES ($1, $2::rag_source, $3, $4, $5, 'https://x')",
            doc_id,
            provider,
            f"{conn_id}-{i}",
            conn_id,
            f"Doc {i}",
        )
        db_run(dsn, "INSERT INTO chunks (document_id, chunk_index, content) VALUES ($1, 0, 'hello')", doc_id)
    return conn_id


def row(dsn, conn_id):
    (r,) = db_run(dsn, "SELECT * FROM oauth_connections WHERE id = $1", conn_id)
    return r


def doc_count(dsn, conn_id):
    return db_run(dsn, "SELECT count(*) AS n FROM documents WHERE connection_id = $1", conn_id)[0]["n"]


def chunk_count(dsn, conn_id):
    return db_run(
        dsn,
        "SELECT count(*) AS n FROM chunks c JOIN documents d ON d.id = c.document_id WHERE d.connection_id = $1",
        conn_id,
    )[0]["n"]


def status_of(client, provider="notion"):
    (s,) = [c for c in client.get("/connections").json() if c["provider"] == provider]
    return s


def session_factory(dsn):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://", 1), poolclass=NullPool)
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


def run_save(dsn, fn, *args):
    async def go():
        async with session_factory(dsn)() as session:
            return await fn(session, *args)

    return asyncio.run(go())


def test_connected_status_reports_document_count(client_for, pg_dsn):
    client = client_for()
    seed(pg_dsn, client.org_id, docs=3)
    s = status_of(client)
    assert s["connected"] is True
    assert s["disconnected"] is False
    assert s["document_count"] == 3
    # Never connected: same shape, nothing counted.
    j = status_of(client, "jira")
    assert j["connected"] is False and j["disconnected"] is False and j["document_count"] == 0


def test_disconnect_and_remove_documents(client_for, pg_dsn):
    client = client_for()
    conn_id = seed(pg_dsn, client.org_id)
    r = client.post(f"/connections/{conn_id}/disconnect", json={"remove_documents": True})
    assert r.status_code == 200, r.text
    assert doc_count(pg_dsn, conn_id) == 0 and chunk_count(pg_dsn, conn_id) == 0
    rw = row(pg_dsn, conn_id)
    assert rw["access_token"] is None and rw["refresh_token"] is None and rw["expires_at"] is None
    assert rw["disconnected_at"] is not None
    s = status_of(client)
    assert s["connected"] is False and s["disconnected"] is True and s["document_count"] == 0
    assert s["id"] == str(conn_id)


def test_disconnect_and_keep_documents(client_for, pg_dsn):
    client = client_for()
    conn_id = seed(pg_dsn, client.org_id)
    r = client.post(f"/connections/{conn_id}/disconnect", json={"remove_documents": False})
    assert r.status_code == 200, r.text
    assert doc_count(pg_dsn, conn_id) == 2 and chunk_count(pg_dsn, conn_id) == 2
    assert row(pg_dsn, conn_id)["access_token"] is None
    s = status_of(client)
    assert s["connected"] is False and s["disconnected"] is True and s["document_count"] == 2


def test_disconnect_is_idempotent(client_for, pg_dsn):
    client = client_for()
    conn_id = seed(pg_dsn, client.org_id)
    assert client.post(f"/connections/{conn_id}/disconnect", json={"remove_documents": False}).status_code == 200
    first = row(pg_dsn, conn_id)["disconnected_at"]
    assert client.post(f"/connections/{conn_id}/disconnect", json={"remove_documents": False}).status_code == 200
    assert row(pg_dsn, conn_id)["disconnected_at"] == first
    assert doc_count(pg_dsn, conn_id) == 2
    # A repeat asking to remove still removes what was kept.
    assert client.post(f"/connections/{conn_id}/disconnect", json={"remove_documents": True}).status_code == 200
    assert doc_count(pg_dsn, conn_id) == 0


def test_disconnect_requires_owner_or_admin(client_for, pg_dsn):
    client = client_for(role="member")
    conn_id = seed(pg_dsn, client.org_id)
    assert client.post(f"/connections/{conn_id}/disconnect", json={"remove_documents": True}).status_code == 403
    assert client.post(f"/connections/{conn_id}/remove-documents").status_code == 403
    assert row(pg_dsn, conn_id)["access_token"] is not None
    assert doc_count(pg_dsn, conn_id) == 2


def test_disconnect_is_org_scoped(client_for, pg_dsn):
    client = client_for()
    other_org = uuid.uuid4()
    db_run(pg_dsn, "INSERT INTO organizations (id, name, slug) VALUES ($1, 'O', $2)", other_org, f"o-{other_org}")
    conn_id = seed(pg_dsn, other_org)
    assert client.post(f"/connections/{conn_id}/disconnect", json={"remove_documents": True}).status_code == 404
    assert client.post(f"/connections/{conn_id}/remove-documents").status_code == 404
    assert row(pg_dsn, conn_id)["disconnected_at"] is None
    assert doc_count(pg_dsn, conn_id) == 2


def test_remove_documents_only_when_disconnected(client_for, pg_dsn):
    client = client_for()
    conn_id = seed(pg_dsn, client.org_id)
    assert client.post(f"/connections/{conn_id}/remove-documents").status_code == 409
    assert doc_count(pg_dsn, conn_id) == 2
    client.post(f"/connections/{conn_id}/disconnect", json={"remove_documents": False})
    r = client.post(f"/connections/{conn_id}/remove-documents")
    assert r.status_code == 200, r.text
    assert r.json()["removed"] == 2
    assert doc_count(pg_dsn, conn_id) == 0 and chunk_count(pg_dsn, conn_id) == 0
    s = status_of(client)
    assert s["disconnected"] is True and s["document_count"] == 0


@pytest.mark.parametrize("provider,label", [("notion", "Notion"), ("jira", "Jira")])
def test_sync_refused_when_disconnected(client_for, pg_dsn, provider, label):
    client = client_for()
    conn_id = seed(pg_dsn, client.org_id, provider=provider)
    client.post(f"/connections/{conn_id}/disconnect", json={"remove_documents": False})
    r = client.post(f"/sync/{provider}")
    assert r.status_code == 409
    assert r.json()["detail"] == f"{label} is disconnected. Connect it again to sync."


def test_preview_refused_when_disconnected(client_for, pg_dsn):
    client = client_for()
    conn_id = seed(pg_dsn, client.org_id)
    client.post(f"/connections/{conn_id}/disconnect", json={"remove_documents": False})
    assert client.get(f"/connections/{conn_id}/preview").status_code == 409


def test_disconnect_clears_jira_needs_reconnect(client_for, pg_dsn):
    client = client_for()
    conn_id = seed(pg_dsn, client.org_id, provider="jira")
    db_run(pg_dsn, "UPDATE oauth_connections SET needs_reconnect_at = now() WHERE id = $1", conn_id)
    assert status_of(client, "jira")["needs_reconnect"] is True
    client.post(f"/connections/{conn_id}/disconnect", json={"remove_documents": False})
    assert row(pg_dsn, conn_id)["needs_reconnect_at"] is None
    assert status_of(client, "jira")["needs_reconnect"] is False


def test_jira_refresh_refuses_disconnected_connection():
    conn = SimpleNamespace(
        id=uuid.uuid4(), access_token=None, refresh_token=None, expires_at=None,
        disconnected_at=datetime.now(timezone.utc),
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(jira_auth.ensure_fresh_token(None, conn))
    assert exc.value.status_code == 409
    assert exc.value.detail == "Jira is disconnected. Connect it again to sync."


def test_auto_sync_skips_disconnected(client_for, pg_dsn, monkeypatch):
    client = client_for()
    live = seed(pg_dsn, client.org_id, provider="notion")
    gone = seed(pg_dsn, client.org_id, provider="jira")
    client.post(f"/connections/{gone}/disconnect", json={"remove_documents": False})

    factory = session_factory(pg_dsn)
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def scope():
        async with factory() as session:
            yield session

    calls = []

    async def fake_sync(session, connection):
        calls.append(connection.id)
        return {"published": 0, "truncated": False}

    monkeypatch.setattr(auto_sync, "_session_scope", scope)
    monkeypatch.setattr(auto_sync, "sync_notion_connection", fake_sync)
    monkeypatch.setattr(auto_sync, "sync_jira_connection", fake_sync)
    asyncio.run(auto_sync._sync_all_connections())
    assert live in calls
    assert gone not in calls


def test_reconnect_same_workspace_keeps_documents(client_for, pg_dsn):
    client = client_for()
    conn_id = seed(pg_dsn, client.org_id, workspace_id="ws-1")
    client.post(f"/connections/{conn_id}/disconnect", json={"remove_documents": False})
    saved = run_save(
        pg_dsn, saving.save_notion_connection, client.org_id,
        {"access_token": "new-token", "workspace_id": "ws-1", "workspace_name": "Acme"},
    )
    assert saved.id == conn_id
    rw = row(pg_dsn, conn_id)
    assert rw["disconnected_at"] is None and rw["access_token"] is not None
    assert doc_count(pg_dsn, conn_id) == 2
    s = status_of(client)
    assert s["connected"] is True and s["disconnected"] is False and s["document_count"] == 2


def test_reconnect_different_workspace_deletes_old_documents(client_for, pg_dsn):
    client = client_for()
    conn_id = seed(pg_dsn, client.org_id, workspace_id="ws-1")
    client.post(f"/connections/{conn_id}/disconnect", json={"remove_documents": False})
    run_save(
        pg_dsn, saving.save_notion_connection, client.org_id,
        {"access_token": "new-token", "workspace_id": "ws-OTHER", "workspace_name": "Other"},
    )
    rw = row(pg_dsn, conn_id)
    assert rw["disconnected_at"] is None and rw["workspace_id"] == "ws-OTHER"
    assert doc_count(pg_dsn, conn_id) == 0


def test_jira_reconnect_same_and_different_site(client_for, pg_dsn):
    client = client_for()
    conn_id = seed(pg_dsn, client.org_id, provider="jira", workspace_id="cloud-1")
    client.post(f"/connections/{conn_id}/disconnect", json={"remove_documents": False})
    token = {"access_token": "a", "refresh_token": "r", "expires_in": 3600}
    run_save(pg_dsn, saving.save_jira_connection, client.org_id, token, {"id": "cloud-1", "url": "https://a.atlassian.net"})
    assert row(pg_dsn, conn_id)["disconnected_at"] is None
    assert doc_count(pg_dsn, conn_id) == 2
    client.post(f"/connections/{conn_id}/disconnect", json={"remove_documents": False})
    run_save(pg_dsn, saving.save_jira_connection, client.org_id, token, {"id": "cloud-2", "url": "https://b.atlassian.net"})
    assert row(pg_dsn, conn_id)["disconnected_at"] is None
    assert doc_count(pg_dsn, conn_id) == 0


def test_notion_secret_form_reconnect_uses_shared_save(client_for, pg_dsn, external):
    """The desktop pasted-secret path reconnects through save_notion_connection."""
    client = client_for()
    conn_id = seed(pg_dsn, client.org_id, workspace_id="ws-1")
    client.post(f"/connections/{conn_id}/disconnect", json={"remove_documents": False})
    external.handler = lambda request: httpx.Response(
        200, json={"id": "bot-9", "bot": {"workspace_id": "ws-2", "workspace_name": "Two"}}
    )
    r = client.post("/connections/notion/token", json={"token": "ntn_secret"})
    assert r.status_code == 200, r.text
    rw = row(pg_dsn, conn_id)
    assert rw["disconnected_at"] is None and rw["workspace_id"] == "ws-2" and rw["bot_id"] == "bot-9"
    assert doc_count(pg_dsn, conn_id) == 0


def test_knowledge_base_labels_disconnected_documents(client_for, pg_dsn):
    client = client_for()
    conn_id = seed(pg_dsn, client.org_id, docs=1)
    items = client.get("/documents").json()["items"]
    assert [i["from_disconnected_source"] for i in items] == [False]
    client.post(f"/connections/{conn_id}/disconnect", json={"remove_documents": False})
    items = client.get("/documents").json()["items"]
    assert [i["from_disconnected_source"] for i in items] == [True]
    detail = client.get(f"/documents/{items[0]['id']}").json()
    assert detail["from_disconnected_source"] is True
    excluded = client.patch(f"/documents/{items[0]['id']}/exclude", json={"excluded": True}).json()
    assert excluded["from_disconnected_source"] is True


@pytest.mark.parametrize("disconnect_midway", [True, False])
def test_jira_refresh_does_not_write_tokens_onto_a_row_disconnected_midway(
    client_for, pg_dsn, external, disconnect_midway
):
    """A disconnect that commits while a Jira refresh is in flight wins: the refreshed
    tokens are discarded and the caller gets the same 409 as Sync now."""
    import threading

    from app.models.rag import OAuthConnection

    client = client_for(mode="server")
    conn_id = seed(pg_dsn, client.org_id, provider="jira")
    db_run(pg_dsn, "UPDATE oauth_connections SET expires_at = now() - interval '5 minutes' WHERE id = $1", conn_id)

    def token_endpoint(request):
        if disconnect_midway:
            # The disconnect commits from another connection while the refresh request is out.
            t = threading.Thread(
                target=client.post, args=(f"/connections/{conn_id}/disconnect",), kwargs={"json": {"remove_documents": False}}
            )
            t.start()
            t.join()
        return httpx.Response(200, json={"access_token": "fresh-access", "refresh_token": "fresh-refresh", "expires_in": 3600})

    external.handler = token_endpoint

    async def go():
        async with session_factory(pg_dsn)() as session:
            connection = await session.get(OAuthConnection, conn_id)
            return await jira_auth.ensure_fresh_token(session, connection)

    rw_before = row(pg_dsn, conn_id)
    if disconnect_midway:
        with pytest.raises(HTTPException) as exc:
            asyncio.run(go())
        assert exc.value.status_code == 409
        assert exc.value.detail == "Jira is disconnected. Connect it again to sync."
        rw = row(pg_dsn, conn_id)
        assert rw["disconnected_at"] is not None
        assert rw["access_token"] is None and rw["refresh_token"] is None and rw["expires_at"] is None
    else:
        assert asyncio.run(go()) == "fresh-access"
        rw = row(pg_dsn, conn_id)
        assert rw["disconnected_at"] is None
        assert rw["access_token"] != rw_before["access_token"] and rw["expires_at"] > datetime.now(timezone.utc)

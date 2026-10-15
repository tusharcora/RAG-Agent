import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException

from app.core.config import settings
from app.core.crypto import decrypt_token, encrypt_token
from app.integrations import broker, jira_auth

RECONNECT = "Jira needs reconnecting. Use Reconnect on the Connections page."


class FakeSession:
    """Stands in for the conditional token UPDATE: matches unless `disconnected`."""

    def __init__(self, disconnected=False):
        self.commits = 0
        self.rollbacks = 0
        self.disconnected = disconnected

    async def execute(self, stmt):
        return SimpleNamespace(first=lambda: None if self.disconnected else ("c1",))

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(settings, "token_encryption_key", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "auth_broker_url", "https://broker.test")
    calls = []

    def install(handler, mode):
        monkeypatch.setattr(settings, "app_mode", mode)

        def wrapped(request):
            calls.append(request)
            return handler(request)

        transport = httpx.MockTransport(wrapped)
        monkeypatch.setattr(broker, "TRANSPORT", transport)
        real = httpx.AsyncClient
        monkeypatch.setattr(
            jira_auth.httpx, "AsyncClient", lambda **kw: real(**{"transport": transport, **kw})
        )

    return install, calls


def expired_connection():
    return SimpleNamespace(
        id="c1",
        access_token=encrypt_token("old-access"),
        refresh_token=encrypt_token("old-refresh"),
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )


def ok(request):
    return httpx.Response(200, json={"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600})


@pytest.mark.asyncio
async def test_desktop_refresh_uses_broker(setup):
    install, calls = setup
    install(ok, "desktop")
    conn, session = expired_connection(), FakeSession()
    token = await jira_auth.ensure_fresh_token(session, conn)
    assert token == "new-access"
    assert [str(c.url) for c in calls] == ["https://broker.test/refresh/jira"]
    assert json.loads(calls[0].content) == {"refresh_token": "old-refresh"}
    assert conn.access_token != "new-access" and decrypt_token(conn.access_token) == "new-access"
    assert decrypt_token(conn.refresh_token) == "new-refresh"
    assert conn.expires_at > datetime.now(timezone.utc) + timedelta(minutes=50)
    assert session.commits == 1


@pytest.mark.asyncio
async def test_server_refresh_uses_atlassian(setup):
    install, calls = setup
    install(ok, "server")
    conn = expired_connection()
    token = await jira_auth.ensure_fresh_token(FakeSession(), conn)
    assert token == "new-access"
    assert [str(c.url) for c in calls] == [jira_auth.JIRA_TOKEN_URL]
    assert json.loads(calls[0].content)["grant_type"] == "refresh_token"
    assert decrypt_token(conn.access_token) == "new-access"


@pytest.mark.asyncio
async def test_desktop_refresh_refused_message(setup):
    install, _ = setup
    install(lambda r: httpx.Response(400, json={"error": "revoked"}), "desktop")
    with pytest.raises(HTTPException) as exc:
        await jira_auth.ensure_fresh_token(FakeSession(), expired_connection())
    assert exc.value.status_code == 400 and exc.value.detail == RECONNECT


@pytest.mark.asyncio
async def test_desktop_refresh_unreachable_is_502(setup):
    install, _ = setup
    install(lambda r: httpx.Response(502, json={"error": "x"}), "desktop")
    with pytest.raises(HTTPException) as exc:
        await jira_auth.ensure_fresh_token(FakeSession(), expired_connection())
    assert exc.value.status_code == 502 and exc.value.detail == broker.UNREACHABLE


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 404, 429, 500, 503])
async def test_desktop_refresh_non_400_is_transient(setup, status):
    # Only the Worker's 400 means "Atlassian refused the token"; nothing else may say Reconnect.
    install, _ = setup
    install(lambda r: httpx.Response(status, json={"error": "x"}), "desktop")
    with pytest.raises(HTTPException) as exc:
        await jira_auth.ensure_fresh_token(FakeSession(), expired_connection())
    assert exc.value.status_code == 502 and exc.value.detail == broker.UNREACHABLE


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 429, 503])
async def test_broker_refresh_status_mapping(setup, status):
    install, _ = setup
    install(lambda r: httpx.Response(status, json={"error": "x"}), "desktop")
    with pytest.raises(broker.BrokerUnavailable):
        await broker.refresh_jira("rt")
    install(lambda r: httpx.Response(400, json={"error": "revoked"}), "desktop")
    with pytest.raises(broker.BrokerRefused):
        await broker.refresh_jira("rt")



def _seed_jira(pg_dsn, *, disconnected=False, needs_reconnect=False):
    import uuid

    from .conftest import db_run

    org_id, conn_id = uuid.uuid4(), uuid.uuid4()
    db_run(pg_dsn, "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T', $2)", org_id, f"t-{org_id}")
    db_run(
        pg_dsn,
        "INSERT INTO oauth_connections (id, org_id, provider, workspace_id, workspace_name, access_token,"
        " refresh_token, expires_at, needs_reconnect_at)"
        " VALUES ($1, $2, 'jira', 'cloud', 'acme', $3, $4, now() - interval '5 minutes',"
        " CASE WHEN $5 THEN now() ELSE NULL END)",
        conn_id,
        org_id,
        encrypt_token("old-access"),
        encrypt_token("old-refresh"),
        needs_reconnect,
    )
    return conn_id


def _flag(pg_dsn, conn_id):
    from .conftest import db_run

    (r,) = db_run(pg_dsn, "SELECT needs_reconnect_at FROM oauth_connections WHERE id = $1", conn_id)
    return r["needs_reconnect_at"] is not None


async def _refresh_with_db(pg_dsn, conn_id):
    """ensure_fresh_token against the real row, in its own session (like a sync request)."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.models.rag import OAuthConnection

    engine = create_async_engine(pg_dsn.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)() as session:
            connection = await session.get(OAuthConnection, conn_id)
            return await jira_auth.ensure_fresh_token(session, connection)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_refusal_sets_column_and_success_clears_it(setup, pg_dsn):
    # Covers Sync now and auto_sync alike: both refresh through ensure_fresh_token before publishing anything.
    import asyncio

    install, _ = setup
    conn_id = await asyncio.to_thread(_seed_jira, pg_dsn)
    install(lambda r: httpx.Response(400, json={"error": "revoked"}), "desktop")
    with pytest.raises(HTTPException) as exc:
        await _refresh_with_db(pg_dsn, conn_id)
    assert exc.value.status_code == 400 and exc.value.detail == RECONNECT
    assert await asyncio.to_thread(_flag, pg_dsn, conn_id)
    # A transient failure leaves the mark alone; a successful refresh clears it.
    install(lambda r: httpx.Response(502, json={"error": "x"}), "desktop")
    with pytest.raises(HTTPException):
        await _refresh_with_db(pg_dsn, conn_id)
    assert await asyncio.to_thread(_flag, pg_dsn, conn_id)
    install(ok, "desktop")
    assert await _refresh_with_db(pg_dsn, conn_id) == "new-access"
    assert not await asyncio.to_thread(_flag, pg_dsn, conn_id)


@pytest.mark.asyncio
async def test_transient_failure_does_not_set_column(setup, pg_dsn):
    import asyncio

    install, _ = setup
    conn_id = await asyncio.to_thread(_seed_jira, pg_dsn)
    install(lambda r: httpx.Response(429, json={"error": "slow down"}), "desktop")
    with pytest.raises(HTTPException):
        await _refresh_with_db(pg_dsn, conn_id)
    assert not await asyncio.to_thread(_flag, pg_dsn, conn_id)


def test_in_memory_flag_is_gone():
    assert not hasattr(jira_auth, "_NEEDS_RECONNECT")
    assert not hasattr(jira_auth, "needs_reconnect")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403])
async def test_web_atlassian_refusal_sets_column(setup, pg_dsn, status):
    import asyncio

    install, calls = setup
    conn_id = await asyncio.to_thread(_seed_jira, pg_dsn)
    install(lambda r: httpx.Response(status, json={"error": "invalid_grant"}), "server")
    with pytest.raises(HTTPException) as exc:
        await _refresh_with_db(pg_dsn, conn_id)
    assert exc.value.status_code == 400 and exc.value.detail == RECONNECT
    assert [str(c.url) for c in calls] == [jira_auth.JIRA_TOKEN_URL]
    assert await asyncio.to_thread(_flag, pg_dsn, conn_id)


@pytest.mark.asyncio
async def test_web_atlassian_server_error_does_not_set_column(setup, pg_dsn):
    import asyncio

    install, _ = setup
    conn_id = await asyncio.to_thread(_seed_jira, pg_dsn)
    install(lambda r: httpx.Response(503, json={"error": "x"}), "server")
    with pytest.raises(httpx.HTTPStatusError):
        await _refresh_with_db(pg_dsn, conn_id)
    assert not await asyncio.to_thread(_flag, pg_dsn, conn_id)

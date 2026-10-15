import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.core.crypto import decrypt_token, encrypt_token
from app.integrations import broker, jira

RECONNECT = "Jira needs reconnecting. Use Reconnect on the Connections page."


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(settings, "token_encryption_key", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "auth_broker_url", "https://broker.test")
    calls = []
    db_row = SimpleNamespace(
        access_token=None, refresh_token=None, expires_at=None, needs_reconnect_at=datetime.now(timezone.utc)
    )
    commits = []

    class FakeSession:
        async def get(self, model, pk, **kw):
            assert kw.get("with_for_update"), "the refresh write must lock the row"
            return db_row

        async def commit(self):
            commits.append(1)

    @asynccontextmanager
    async def fake_get_session():
        yield FakeSession()

    monkeypatch.setattr(jira, "get_session", fake_get_session)

    def install(handler, mode):
        monkeypatch.setattr(settings, "app_mode", mode)

        def wrapped(request):
            calls.append(request)
            return handler(request)

        transport = httpx.MockTransport(wrapped)
        monkeypatch.setattr(broker, "TRANSPORT", transport)
        real = httpx.AsyncClient
        monkeypatch.setattr(jira.httpx, "AsyncClient", lambda **kw: real(**{"transport": transport, **kw}))

    return install, calls, db_row, commits


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
    install, calls, db_row, commits = setup
    install(ok, "desktop")
    conn = expired_connection()
    token = await jira._ensure_fresh_token(conn)
    assert token == "new-access"
    assert [str(c.url) for c in calls] == ["https://broker.test/refresh/jira"]
    assert json.loads(calls[0].content) == {"refresh_token": "old-refresh"}
    for row in (db_row, conn):
        assert row.access_token != "new-access" and decrypt_token(row.access_token) == "new-access"
        assert decrypt_token(row.refresh_token) == "new-refresh"
        assert row.expires_at > datetime.now(timezone.utc) + timedelta(minutes=50)
    assert commits == [1]
    assert db_row.needs_reconnect_at is None  # a successful refresh clears the persisted flag


@pytest.mark.asyncio
async def test_server_refresh_uses_atlassian(setup):
    install, calls, db_row, _ = setup
    install(ok, "server")
    token = await jira._ensure_fresh_token(expired_connection())
    assert token == "new-access"
    assert [str(c.url) for c in calls] == [jira.JIRA_TOKEN_URL]
    assert decrypt_token(db_row.access_token) == "new-access"


@pytest.mark.asyncio
async def test_desktop_refresh_refused_message(setup):
    install, _, _, _ = setup
    install(lambda r: httpx.Response(400, json={"error": "revoked"}), "desktop")
    with pytest.raises(RuntimeError, match=RECONNECT):
        await jira._ensure_fresh_token(expired_connection())


@pytest.mark.asyncio
async def test_desktop_refresh_unavailable_propagates(setup):
    install, _, _, _ = setup
    install(lambda r: httpx.Response(502, json={"error": "x"}), "desktop")
    with pytest.raises(broker.BrokerUnavailable):
        await jira._ensure_fresh_token(expired_connection())


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 404, 429, 500, 503])
async def test_desktop_refresh_non_400_is_transient(setup, status):
    # Only the Worker's 400 means "Atlassian refused the token"; nothing else may say Reconnect.
    install, _, _, _ = setup
    install(lambda r: httpx.Response(status, json={"error": "x"}), "desktop")
    with pytest.raises(broker.BrokerUnavailable):
        await jira._ensure_fresh_token(expired_connection())


@pytest.fixture
def real_db(pg_dsn, monkeypatch):
    import asyncio

    from app.core import db

    monkeypatch.setattr(settings, "token_encryption_key", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "auth_broker_url", "https://broker.test")
    monkeypatch.setattr(settings, "app_mode", "desktop")
    monkeypatch.setattr(settings, "database_url", pg_dsn.replace("postgresql://", "postgresql+asyncpg://", 1))

    def seed(needs_reconnect=False):
        import uuid

        from conftest import db_run

        org_id, conn_id = uuid.uuid4(), uuid.uuid4()
        db_run(pg_dsn, "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T', $2)", org_id, f"t-{org_id}")
        db_run(
            pg_dsn,
            "INSERT INTO oauth_connections (id, org_id, provider, workspace_id, workspace_name, access_token,"
            " refresh_token, expires_at, needs_reconnect_at) VALUES ($1, $2, 'jira', 'cloud', 'acme', $3, $4,"
            " now() - interval '5 minutes', CASE WHEN $5 THEN now() ELSE NULL END)",
            conn_id, org_id, encrypt_token("old-access"), encrypt_token("old-refresh"), needs_reconnect,
        )
        return SimpleNamespace(
            id=conn_id,
            access_token=encrypt_token("old-access"),
            refresh_token=encrypt_token("old-refresh"),
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )

    def flag(conn_id):
        from conftest import db_run

        (r,) = db_run(pg_dsn, "SELECT needs_reconnect_at FROM oauth_connections WHERE id = $1", conn_id)
        return r["needs_reconnect_at"] is not None

    def respond(handler):
        monkeypatch.setattr(broker, "TRANSPORT", httpx.MockTransport(handler))

    async def run(coro_fn):
        await db.init_engine()
        try:
            return await coro_fn()
        finally:
            await db.dispose_engine()

    return SimpleNamespace(seed=seed, flag=flag, respond=respond, run=run, to_thread=asyncio.to_thread)


@pytest.mark.asyncio
async def test_jira_worker_refusal_sets_column(real_db):
    conn = await real_db.to_thread(real_db.seed)
    real_db.respond(lambda r: httpx.Response(400, json={"error": "revoked"}))
    with pytest.raises(RuntimeError, match=RECONNECT):
        await real_db.run(lambda: jira._ensure_fresh_token(conn))
    assert await real_db.to_thread(real_db.flag, conn.id)


@pytest.mark.asyncio
async def test_jira_worker_success_clears_column_and_transient_keeps_it(real_db):
    conn = await real_db.to_thread(real_db.seed, True)
    real_db.respond(lambda r: httpx.Response(503, json={"error": "x"}))
    with pytest.raises(broker.BrokerUnavailable):
        await real_db.run(lambda: jira._ensure_fresh_token(conn))
    assert await real_db.to_thread(real_db.flag, conn.id)
    real_db.respond(ok)
    assert await real_db.run(lambda: jira._ensure_fresh_token(conn)) == "new-access"
    assert not await real_db.to_thread(real_db.flag, conn.id)

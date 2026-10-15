"""The worker's GitHub token refresh (app/integrations/github.py): same rules as the
api's github_auth.py, raising module exceptions instead of HTTPException."""

import asyncio
import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

from app.core import db
from app.core.config import settings
from app.core.crypto import decrypt_token, encrypt_token
from app.integrations import broker, github
from conftest import db_run

RECONNECT = "GitHub needs reconnecting. Use Reconnect on the Connections page."
BROKER = "https://broker.test"


@pytest_asyncio.fixture
async def engine(pg_dsn, monkeypatch):
    monkeypatch.setattr(settings, "token_encryption_key", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "auth_broker_url", BROKER)
    monkeypatch.setattr(settings, "github_source_client_id", "web-client")
    monkeypatch.setattr(settings, "github_source_client_secret", "web-secret")
    monkeypatch.setattr(settings, "database_url", pg_dsn.replace("postgresql://", "postgresql+asyncpg://", 1))
    await db.init_engine()
    yield
    await db.dispose_engine()


@pytest.fixture
def fake(monkeypatch):
    class Fake:
        handler = None

    f = Fake()
    f.calls = []

    async def handle(request):
        f.calls.append(request)
        return await f.handler(request)

    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(broker, "TRANSPORT", transport)
    real = httpx.AsyncClient
    monkeypatch.setattr(github.httpx, "AsyncClient", lambda **kw: real(**{"transport": transport, **kw}))
    return f


def rotated(access="new-access"):
    async def handler(request):
        return httpx.Response(200, json={"access_token": access, "refresh_token": "new-refresh", "expires_in": 28800})

    return handler


async def seed(pg_dsn, *, access="A", refresh="R", expires_in=timedelta(hours=1), disconnected=False,
               needs_reconnect=False):
    org_id, conn_id = uuid.uuid4(), uuid.uuid4()

    def go():
        db_run(pg_dsn, "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T', $2)", org_id, f"t-{org_id}")
        db_run(
            pg_dsn,
            "INSERT INTO oauth_connections (id, org_id, provider, workspace_id, workspace_name, access_token,"
            " refresh_token, expires_at, disconnected_at, needs_reconnect_at)"
            " VALUES ($1, $2, 'github', '42', 'octo', $3, $4, $5, $6, $7)",
            conn_id,
            org_id,
            None if disconnected else encrypt_token(access),
            None if disconnected else encrypt_token(refresh),
            None if disconnected else datetime.now(timezone.utc) + expires_in,
            datetime.now(timezone.utc) if disconnected else None,
            datetime.now(timezone.utc) if needs_reconnect else None,
        )

    await asyncio.to_thread(go)
    return conn_id


async def row(pg_dsn, conn_id):
    (r,) = await asyncio.to_thread(db_run, pg_dsn, "SELECT * FROM oauth_connections WHERE id = $1", conn_id)
    return r


@pytest.mark.asyncio
async def test_refresh_reuses_rotated_token(engine, fake, pg_dsn, monkeypatch):
    monkeypatch.setattr(settings, "app_mode", "desktop")
    fake.handler = rotated()
    conn_id = await seed(pg_dsn, access="B")
    assert await github.github_access_token(conn_id, failed_token="A") == "B"
    assert fake.calls == []


@pytest.mark.asyncio
async def test_401_with_unexpired_revoked_token_refreshes(engine, fake, pg_dsn, monkeypatch):
    monkeypatch.setattr(settings, "app_mode", "desktop")
    fake.handler = rotated()
    conn_id = await seed(pg_dsn, access="A")
    assert await github.github_access_token(conn_id, failed_token="A") == "new-access"
    assert len(fake.calls) == 1
    r = await row(pg_dsn, conn_id)
    assert decrypt_token(r["access_token"]) == "new-access" and decrypt_token(r["refresh_token"]) == "new-refresh"


@pytest.mark.asyncio
async def test_concurrent_refreshers_call_github_once(engine, fake, pg_dsn, monkeypatch):
    monkeypatch.setattr(settings, "app_mode", "desktop")
    counter = iter(range(1, 100))

    async def slow(request):
        await asyncio.sleep(0.3)
        n = next(counter)
        return httpx.Response(200, json={"access_token": f"new-{n}", "refresh_token": f"r-{n}", "expires_in": 28800})

    fake.handler = slow
    conn_id = await seed(pg_dsn, expires_in=-timedelta(minutes=1))
    a, b = await asyncio.gather(github.github_access_token(conn_id), github.github_access_token(conn_id))
    assert len(fake.calls) == 1
    assert a == b == "new-1"


@pytest.mark.asyncio
async def test_desktop_refresh_goes_through_broker(engine, fake, pg_dsn, monkeypatch):
    monkeypatch.setattr(settings, "app_mode", "desktop")
    fake.handler = rotated()
    conn_id = await seed(pg_dsn, refresh="R", expires_in=-timedelta(minutes=1))
    await github.github_access_token(conn_id)
    (call,) = fake.calls
    assert str(call.url) == f"{BROKER}/refresh/github_repos"
    assert json.loads(call.content) == {"refresh_token": "R"}


@pytest.mark.asyncio
async def test_web_refresh_goes_to_github_directly(engine, fake, pg_dsn, monkeypatch):
    monkeypatch.setattr(settings, "app_mode", "server")
    fake.handler = rotated()
    conn_id = await seed(pg_dsn, refresh="R", expires_in=-timedelta(minutes=1))
    assert await github.github_access_token(conn_id) == "new-access"
    (call,) = fake.calls
    assert str(call.url) == "https://github.com/login/oauth/access_token"
    assert call.headers["accept"] == "application/json"
    assert {k: v[0] for k, v in parse_qs(call.content.decode()).items()} == {
        "grant_type": "refresh_token", "client_id": "web-client", "client_secret": "web-secret", "refresh_token": "R",
    }


@pytest.mark.asyncio
async def test_200_with_bad_refresh_token_is_refused(engine, fake, pg_dsn, monkeypatch):
    monkeypatch.setattr(settings, "app_mode", "server")

    async def bad(request):
        return httpx.Response(200, json={"error": "bad_refresh_token"})

    fake.handler = bad
    conn_id = await seed(pg_dsn, expires_in=-timedelta(minutes=1))
    with pytest.raises(github.GithubNeedsReconnect, match=RECONNECT):
        await github.github_access_token(conn_id)
    assert (await row(pg_dsn, conn_id))["needs_reconnect_at"] is not None


@pytest.mark.asyncio
async def test_desktop_refused_sets_needs_reconnect(engine, fake, pg_dsn, monkeypatch):
    monkeypatch.setattr(settings, "app_mode", "desktop")

    async def refused(request):
        return httpx.Response(400, json={"error": "GitHub refused"})

    fake.handler = refused
    conn_id = await seed(pg_dsn, expires_in=-timedelta(minutes=1))
    with pytest.raises(github.GithubNeedsReconnect):
        await github.github_access_token(conn_id)
    assert (await row(pg_dsn, conn_id))["needs_reconnect_at"] is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 429, 502])
async def test_unavailable_does_not_flag(engine, fake, pg_dsn, monkeypatch, status):
    monkeypatch.setattr(settings, "app_mode", "desktop")

    async def err(request):
        return httpx.Response(status, json={"error": "x"})

    fake.handler = err
    conn_id = await seed(pg_dsn, expires_in=-timedelta(minutes=1))
    with pytest.raises(github.GithubUnavailable):
        await github.github_access_token(conn_id)
    assert (await row(pg_dsn, conn_id))["needs_reconnect_at"] is None


@pytest.mark.asyncio
async def test_successful_refresh_clears_needs_reconnect(engine, fake, pg_dsn, monkeypatch):
    monkeypatch.setattr(settings, "app_mode", "desktop")
    fake.handler = rotated()
    conn_id = await seed(pg_dsn, expires_in=-timedelta(minutes=1), needs_reconnect=True)
    await github.github_access_token(conn_id)
    assert (await row(pg_dsn, conn_id))["needs_reconnect_at"] is None


@pytest.mark.asyncio
async def test_disconnected_raises_without_refresh(engine, fake, pg_dsn, monkeypatch):
    monkeypatch.setattr(settings, "app_mode", "desktop")
    fake.handler = rotated()
    conn_id = await seed(pg_dsn, disconnected=True)
    with pytest.raises(github.GithubDisconnected):
        await github.github_access_token(conn_id)
    assert fake.calls == []


@pytest.mark.asyncio
async def test_refresh_does_not_write_tokens_onto_disconnected_row(engine, fake, pg_dsn, monkeypatch):
    monkeypatch.setattr(settings, "app_mode", "desktop")
    conn_id = await seed(pg_dsn, expires_in=-timedelta(minutes=1))
    done = threading.Event()

    def disconnect():
        db_run(
            pg_dsn,
            "UPDATE oauth_connections SET access_token = NULL, refresh_token = NULL, expires_at = NULL,"
            " disconnected_at = now() WHERE id = $1",
            conn_id,
        )
        done.set()

    async def during(request):
        threading.Thread(target=disconnect, daemon=True).start()
        await asyncio.sleep(0.3)
        assert not done.is_set(), "the disconnect must wait for the refresh's row lock"
        return httpx.Response(200, json={"access_token": "new-access", "refresh_token": "nr", "expires_in": 28800})

    fake.handler = during
    await github.github_access_token(conn_id)
    await asyncio.to_thread(done.wait, 10)
    r = await row(pg_dsn, conn_id)
    assert r["disconnected_at"] is not None and r["access_token"] is None and r["refresh_token"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status,exc", [(400, broker.BrokerRefused), (404, broker.BrokerUnavailable),
                                         (503, broker.BrokerUnavailable)])
async def test_broker_refresh_github_repos_status_mapping(monkeypatch, status, exc):
    monkeypatch.setattr(settings, "auth_broker_url", BROKER)
    monkeypatch.setattr(broker, "TRANSPORT", httpx.MockTransport(lambda r: httpx.Response(status, json={"error": "x"})))
    with pytest.raises(exc):
        await broker.refresh_github_repos("rt")

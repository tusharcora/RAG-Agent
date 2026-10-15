"""GitHub token refresh (app/integrations/github_auth.py): own locked session,
reuse of a token another process already rotated, persisted needs-reconnect."""

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
from fastapi import HTTPException

from app.api.routes import connections as connections_routes
from app.core import db
from app.core.config import settings
from app.core.crypto import decrypt_token, encrypt_token
from app.integrations import broker, github_auth

from .conftest import db_run

RECONNECT = "GitHub needs reconnecting. Use Reconnect on the Connections page."
BROKER = "https://broker.test"


@pytest_asyncio.fixture
async def factory(pg_dsn, monkeypatch):
    """Points app.core.db's session factory (what github_access_token opens) at the test database."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    monkeypatch.setattr(settings, "token_encryption_key", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "auth_broker_url", BROKER)
    monkeypatch.setattr(settings, "github_source_client_id", "web-client")
    monkeypatch.setattr(settings, "github_source_client_secret", "web-secret")
    engine = create_async_engine(dsn_url(pg_dsn))
    made = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(db, "_session_factory", made)
    yield made
    await engine.dispose()


def dsn_url(dsn):
    return dsn.replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.fixture
def github(monkeypatch):
    """Records every refresh call (broker or GitHub) and answers with `.handler`."""

    class Fake:
        calls: list[httpx.Request] = []
        handler = None

    fake = Fake()
    fake.calls = []

    async def handle(request):
        fake.calls.append(request)
        return await fake.handler(request)

    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(broker, "TRANSPORT", transport)
    real = httpx.AsyncClient
    monkeypatch.setattr(github_auth.httpx, "AsyncClient", lambda **kw: real(**{"transport": transport, **kw}))
    return fake


def rotated(access="new-access", refresh="new-refresh"):
    async def handler(request):
        return httpx.Response(
            200,
            json={"access_token": access, "refresh_token": refresh, "expires_in": 28800,
                  "refresh_token_expires_in": 15897600},
        )

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
    return org_id, conn_id


async def row(pg_dsn, conn_id):
    (r,) = await asyncio.to_thread(db_run, pg_dsn, "SELECT * FROM oauth_connections WHERE id = $1", conn_id)
    return r


@pytest.mark.asyncio
async def test_refresh_reuses_rotated_token(factory, github, pg_dsn, monkeypatch):
    # Another process already rotated A → B; our 401 was with A, so B is reused without a refresh.
    monkeypatch.setattr(settings, "app_mode", "desktop")
    github.handler = rotated()
    _, conn_id = await seed(pg_dsn, access="B")
    assert await github_auth.github_access_token(conn_id, failed_token="A") == "B"
    assert github.calls == []


@pytest.mark.asyncio
async def test_valid_token_returned_without_refresh(factory, github, pg_dsn, monkeypatch):
    monkeypatch.setattr(settings, "app_mode", "desktop")
    github.handler = rotated()
    _, conn_id = await seed(pg_dsn, access="A")
    assert await github_auth.github_access_token(conn_id) == "A"
    assert github.calls == []


@pytest.mark.asyncio
async def test_401_with_unexpired_revoked_token_refreshes(factory, github, pg_dsn, monkeypatch):
    monkeypatch.setattr(settings, "app_mode", "desktop")
    github.handler = rotated()
    _, conn_id = await seed(pg_dsn, access="A", refresh="R")
    assert await github_auth.github_access_token(conn_id, failed_token="A") == "new-access"
    assert len(github.calls) == 1
    r = await row(pg_dsn, conn_id)
    assert decrypt_token(r["access_token"]) == "new-access"
    assert decrypt_token(r["refresh_token"]) == "new-refresh"
    assert r["expires_at"] > datetime.now(timezone.utc) + timedelta(hours=7)


@pytest.mark.asyncio
async def test_token_expiring_within_5_minutes_refreshes(factory, github, pg_dsn, monkeypatch):
    monkeypatch.setattr(settings, "app_mode", "desktop")
    github.handler = rotated()
    _, conn_id = await seed(pg_dsn, expires_in=timedelta(minutes=4))
    assert await github_auth.github_access_token(conn_id) == "new-access"
    assert len(github.calls) == 1


@pytest.mark.asyncio
async def test_concurrent_refreshers_call_github_once(factory, github, pg_dsn, monkeypatch):
    monkeypatch.setattr(settings, "app_mode", "desktop")
    counter = iter(range(1, 100))

    async def slow(request):
        await asyncio.sleep(0.3)  # the second caller arrives while the first holds the row lock
        n = next(counter)
        return httpx.Response(200, json={"access_token": f"new-{n}", "refresh_token": f"r-{n}", "expires_in": 28800})

    github.handler = slow
    _, conn_id = await seed(pg_dsn, expires_in=-timedelta(minutes=1))
    a, b = await asyncio.gather(
        github_auth.github_access_token(conn_id), github_auth.github_access_token(conn_id)
    )
    assert len(github.calls) == 1
    assert a == b == "new-1"


@pytest.mark.asyncio
async def test_desktop_refresh_goes_through_broker(factory, github, pg_dsn, monkeypatch):
    monkeypatch.setattr(settings, "app_mode", "desktop")
    github.handler = rotated()
    _, conn_id = await seed(pg_dsn, refresh="R", expires_in=-timedelta(minutes=1))
    await github_auth.github_access_token(conn_id)
    (call,) = github.calls
    assert str(call.url) == f"{BROKER}/refresh/github_repos"
    assert json.loads(call.content) == {"refresh_token": "R"}


@pytest.mark.asyncio
async def test_web_refresh_goes_to_github_directly(factory, github, pg_dsn, monkeypatch):
    monkeypatch.setattr(settings, "app_mode", "server")
    github.handler = rotated()
    _, conn_id = await seed(pg_dsn, refresh="R", expires_in=-timedelta(minutes=1))
    assert await github_auth.github_access_token(conn_id) == "new-access"
    (call,) = github.calls
    assert str(call.url) == "https://github.com/login/oauth/access_token"
    assert call.method == "POST"
    assert call.headers["accept"] == "application/json"
    body = {k: v[0] for k, v in parse_qs(call.content.decode()).items()}
    assert body == {
        "grant_type": "refresh_token",
        "client_id": "web-client",
        "client_secret": "web-secret",
        "refresh_token": "R",
    }


@pytest.mark.asyncio
async def test_200_with_bad_refresh_token_is_refused(factory, github, pg_dsn, monkeypatch):
    # GitHub answers a refused refresh with 200 and an error field.
    monkeypatch.setattr(settings, "app_mode", "server")

    async def bad(request):
        return httpx.Response(200, json={"error": "bad_refresh_token", "error_description": "x"})

    github.handler = bad
    _, conn_id = await seed(pg_dsn, expires_in=-timedelta(minutes=1))
    with pytest.raises(HTTPException) as exc:
        await github_auth.github_access_token(conn_id)
    assert exc.value.status_code == 409 and exc.value.detail == RECONNECT
    assert (await row(pg_dsn, conn_id))["needs_reconnect_at"] is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{"error": "incorrect_client_credentials"}, {"nope": 1}])
async def test_web_other_errors_are_unavailable(factory, github, pg_dsn, monkeypatch, body):
    monkeypatch.setattr(settings, "app_mode", "server")

    async def err(request):
        return httpx.Response(200, json=body)

    github.handler = err
    _, conn_id = await seed(pg_dsn, expires_in=-timedelta(minutes=1))
    with pytest.raises(HTTPException) as exc:
        await github_auth.github_access_token(conn_id)
    assert exc.value.status_code == 502
    assert (await row(pg_dsn, conn_id))["needs_reconnect_at"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 429, 500, 502, 503])
async def test_desktop_non_400_is_unavailable_without_flag(factory, github, pg_dsn, monkeypatch, status):
    monkeypatch.setattr(settings, "app_mode", "desktop")

    async def err(request):
        return httpx.Response(status, json={"error": "x"})

    github.handler = err
    _, conn_id = await seed(pg_dsn, expires_in=-timedelta(minutes=1))
    with pytest.raises(HTTPException) as exc:
        await github_auth.github_access_token(conn_id)
    assert exc.value.status_code == 502 and exc.value.detail == broker.UNREACHABLE
    r = await row(pg_dsn, conn_id)
    assert r["needs_reconnect_at"] is None and decrypt_token(r["access_token"]) == "A"


@pytest.mark.asyncio
async def test_successful_refresh_clears_needs_reconnect(factory, github, pg_dsn, monkeypatch):
    monkeypatch.setattr(settings, "app_mode", "desktop")
    github.handler = rotated()
    _, conn_id = await seed(pg_dsn, expires_in=-timedelta(minutes=1), needs_reconnect=True)
    await github_auth.github_access_token(conn_id)
    assert (await row(pg_dsn, conn_id))["needs_reconnect_at"] is None


@pytest.mark.asyncio
async def test_disconnected_connection_is_409_without_refresh(factory, github, pg_dsn, monkeypatch):
    monkeypatch.setattr(settings, "app_mode", "desktop")
    github.handler = rotated()
    _, conn_id = await seed(pg_dsn, disconnected=True)
    with pytest.raises(HTTPException) as exc:
        await github_auth.github_access_token(conn_id)
    assert exc.value.status_code == 409
    assert exc.value.detail == "GitHub is disconnected. Connect it again to sync."
    assert github.calls == []


@pytest.mark.asyncio
async def test_refresh_does_not_write_tokens_onto_disconnected_row(factory, github, pg_dsn, monkeypatch):
    # A disconnect issued while the refresh is in flight waits on the row lock and then wipes the tokens;
    # the refreshed tokens never survive on a disconnected row.
    monkeypatch.setattr(settings, "app_mode", "desktop")
    _, conn_id = await seed(pg_dsn, expires_in=-timedelta(minutes=1))
    disconnect_done = threading.Event()

    def disconnect():
        db_run(
            pg_dsn,
            "UPDATE oauth_connections SET access_token = NULL, refresh_token = NULL, expires_at = NULL,"
            " disconnected_at = now() WHERE id = $1",
            conn_id,
        )
        disconnect_done.set()

    async def during(request):
        threading.Thread(target=disconnect, daemon=True).start()
        await asyncio.sleep(0.3)
        assert not disconnect_done.is_set(), "the disconnect must wait for the refresh's row lock"
        return httpx.Response(200, json={"access_token": "new-access", "refresh_token": "nr", "expires_in": 28800})

    github.handler = during
    await github_auth.github_access_token(conn_id)
    await asyncio.to_thread(disconnect_done.wait, 10)
    r = await row(pg_dsn, conn_id)
    assert r["disconnected_at"] is not None
    assert r["access_token"] is None and r["refresh_token"] is None


def test_refused_sets_needs_reconnect_and_get_connections_reports_it(make_client, pg_dsn, monkeypatch):
    client = make_client(db=True, routers=[(connections_routes.router, "/connections")])
    conn_id = uuid.uuid4()
    db_run(
        pg_dsn,
        "INSERT INTO oauth_connections (id, org_id, provider, workspace_id, workspace_name, access_token,"
        " refresh_token, expires_at) VALUES ($1, $2, 'github', '42', 'octo', $3, $4, now() - interval '1 minute')",
        conn_id,
        client.org_id,
        encrypt_token("A"),
        encrypt_token("R"),
    )
    monkeypatch.setattr(settings, "auth_broker_url", BROKER)
    monkeypatch.setattr(broker, "TRANSPORT", httpx.MockTransport(
        lambda r: httpx.Response(400, json={"error": "GitHub refused the refresh."})
    ))

    def status():
        (s,) = [c for c in client.get("/connections").json() if c["provider"] == "github"]
        return s

    assert status()["needs_reconnect"] is False

    async def refresh():
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

        engine = create_async_engine(dsn_url(pg_dsn))
        monkeypatch.setattr(db, "_session_factory", async_sessionmaker(engine, expire_on_commit=False,
                                                                        class_=AsyncSession))
        try:
            with pytest.raises(HTTPException) as exc:
                await github_auth.github_access_token(conn_id)
        finally:
            await engine.dispose()
        return exc.value

    err = asyncio.run(refresh())
    assert err.status_code == 409 and err.detail == RECONNECT
    assert status()["needs_reconnect"] is True


def test_disconnect_clears_needs_reconnect(make_client, pg_dsn):
    client = make_client(db=True, routers=[(connections_routes.router, "/connections")])
    conn_id = uuid.uuid4()
    db_run(
        pg_dsn,
        "INSERT INTO oauth_connections (id, org_id, provider, workspace_id, workspace_name, access_token,"
        " refresh_token, expires_at, needs_reconnect_at)"
        " VALUES ($1, $2, 'github', '42', 'octo', $3, $4, now() + interval '1 hour', now())",
        conn_id,
        client.org_id,
        encrypt_token("A"),
        encrypt_token("R"),
    )
    r = client.post(f"/connections/{conn_id}/disconnect", json={"remove_documents": False})
    assert r.status_code == 200
    (stored,) = db_run(pg_dsn, "SELECT needs_reconnect_at FROM oauth_connections WHERE id = $1", conn_id)
    assert stored["needs_reconnect_at"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status,exc", [(400, broker.BrokerRefused), (404, broker.BrokerUnavailable),
                                         (502, broker.BrokerUnavailable)])
async def test_broker_refresh_github_repos_status_mapping(monkeypatch, status, exc):
    monkeypatch.setattr(settings, "auth_broker_url", BROKER)
    monkeypatch.setattr(broker, "TRANSPORT", httpx.MockTransport(lambda r: httpx.Response(status, json={"error": "x"})))
    with pytest.raises(exc):
        await broker.refresh_github_repos("rt")

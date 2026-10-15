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
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


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

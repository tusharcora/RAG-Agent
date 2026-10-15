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
    db_row = SimpleNamespace(access_token=None, refresh_token=None, expires_at=None)
    commits = []

    class FakeSession:
        async def get(self, model, pk):
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

"""Desktop Connect flow: /connect/* routes talking to the sign-in Worker
(desktop/broker) through a fake transport."""

import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.fernet import Fernet

from app.api.routes import connect as connect_routes
from app.api.routes import notion_oauth
from app.core.config import settings
from app.integrations import broker

from .conftest import db_run

BROKER = "https://broker.example.workers.dev"
PORT = 8765
UNREACHABLE = "Couldn't reach the sign-in service. Check your internet connection."
EXPIRED = "This sign-in link has expired. Start again from the app."

NOTION_RESULT = {
    "provider": "notion",
    "access_token": "secret-notion-token",
    "workspace_id": "ws-1",
    "workspace_name": "Acme Notes",
    "bot_id": "bot-1",
}
JIRA_RESULT = {
    "provider": "jira",
    "access_token": "secret-jira-token",
    "refresh_token": "secret-jira-refresh",
    "expires_in": 3600,
    "cloud_id": "cloud-123",
    "site_url": "https://acme.atlassian.net",
    "site_name": "acme",
}


class FakeBroker:
    """Stands in for the Worker: /health, /redeem. `results` maps a state to
    the result it releases once; `unreachable` makes every call fail."""

    def __init__(self):
        self.health_status = 200
        self.unreachable = False
        self.results: dict[str, dict] = {}
        self.redeemed: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.unreachable:
            raise httpx.ConnectError("no route", request=request)
        assert request.url.host == "broker.example.workers.dev"
        if request.url.path == "/health":
            return httpx.Response(self.health_status, text="ok")
        if request.url.path == "/redeem":
            body = json.loads(request.content)
            self.redeemed.append(body)
            entry = self.results.pop(body["state"], None)
            if entry is None:
                return httpx.Response(404, json={"error": EXPIRED})
            if _challenge(body["verifier"]) != entry["challenge"] or body.get("pickup") != pickup_for(body["state"]):
                return httpx.Response(403, json={"error": "This sign-in couldn't be confirmed."})
            return httpx.Response(200, json=entry["result"])
        return httpx.Response(404, text="not found")


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def pickup_for(state: str) -> str:
    """The one-time pickup the fake Worker put in the loopback redirect for `state`."""
    return _challenge("pickup:" + state)


def fin(state: str) -> dict:
    """Query params of the Worker's loopback redirect for `state`."""
    return {"state": state, "pickup": pickup_for(state)}


@pytest.fixture
def fake_broker(monkeypatch):
    fb = FakeBroker()
    monkeypatch.setattr(broker, "TRANSPORT", httpx.MockTransport(fb.handler))
    monkeypatch.setattr(settings, "auth_broker_url", BROKER)
    connect_routes._FLOWS.clear()
    monkeypatch.setattr(connect_routes, "IDENTITY_HANDLER", None)
    yield fb
    connect_routes._FLOWS.clear()


@pytest.fixture
def client_for(make_client):
    def _make(**kw):
        return make_client(
            routers=[(connect_routes.router, "/connect")], base_url=f"http://127.0.0.1:{PORT}", **kw
        )

    return _make


def _start(client, fb, purpose, result):
    """Start a flow and have the fake Worker hold `result` for it, as if the
    person had finished on the provider's page."""
    r = client.post(f"/connect/{purpose}/start")
    assert r.status_code == 200, r.text
    state = r.json()["state"]
    challenge = parse_qs(urlsplit(r.json()["url"]).query)["challenge"][0]
    fb.results[state] = {"challenge": challenge, "result": result}
    return state


def _rows(dsn, org_id, provider):
    return db_run(dsn, "SELECT * FROM oauth_connections WHERE org_id = $1 AND provider = $2", org_id, provider)


def test_routes_404_in_server_mode(client_for, fake_broker):
    client = client_for(mode="server")
    assert client.post("/connect/notion/start").status_code == 404
    assert client.get("/connect/finish", params={"state": "x" * 43}).status_code == 404
    assert client.get("/connect/status/abc").status_code == 404
    assert client.post("/connect/abc/cancel").status_code == 404


def test_start_requires_owner(client_for, fake_broker):
    assert client_for(role="member").post("/connect/notion/start").status_code == 403


def test_start_unknown_or_identity_purpose_404(client_for, fake_broker):
    client = client_for()
    assert client.post("/connect/dropbox/start").status_code == 404
    # google/github only once Task 4 sets IDENTITY_HANDLER.
    assert client.post("/connect/google/start").status_code == 404
    assert client.post("/connect/github/start").status_code == 404


def test_start_requires_configured_broker(client_for, fake_broker, monkeypatch):
    monkeypatch.setattr(settings, "auth_broker_url", "")
    r = client_for().post("/connect/notion/start")
    assert r.status_code == 409
    assert r.json() == {"detail": "Sign-in service isn't configured."}


def test_start_broker_unreachable(client_for, fake_broker):
    fake_broker.unreachable = True
    r = client_for().post("/connect/notion/start")
    assert r.status_code == 502
    assert r.json()["detail"] == UNREACHABLE
    assert connect_routes._FLOWS == {}


def test_start_broker_unhealthy(client_for, fake_broker):
    fake_broker.health_status = 502
    r = client_for().post("/connect/jira/start")
    assert r.status_code == 502 and r.json()["detail"] == UNREACHABLE
    assert connect_routes._FLOWS == {}


def test_start_returns_url_with_challenge(client_for, fake_broker):
    r = client_for().post("/connect/notion/start")
    assert r.status_code == 200
    body = r.json()
    url = urlsplit(body["url"])
    assert f"{url.scheme}://{url.netloc}" == BROKER
    assert url.path == "/start/notion"
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert q["state"] == body["state"]
    assert 32 <= len(body["state"]) <= 128
    flow = connect_routes._FLOWS[body["state"]]
    assert flow.purpose == "notion" and flow.status == "pending"
    assert q["challenge"] == _challenge(flow.verifier) and len(q["challenge"]) == 43
    assert q["port"] == str(PORT)
    assert flow.verifier not in body["url"]
    assert "verifier" not in body


def test_finish_notion_saves_connection(client_for, fake_broker, pg_dsn):
    client = client_for(db=True)
    state = _start(client, fake_broker, "notion", NOTION_RESULT)
    r = client.get("/connect/finish", params=fin(state))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert "Connected to Notion" in r.text  # tab title
    assert "Notion is <em>connected</em>." in r.text
    assert "You can close this tab." in r.text
    assert "secret-notion-token" not in r.text and "<script" not in r.text
    rows = _rows(pg_dsn, client.org_id, "notion")
    assert len(rows) == 1
    row = rows[0]
    assert row["workspace_name"] == "Acme Notes" and row["workspace_id"] == "ws-1" and row["bot_id"] == "bot-1"
    assert row["access_token"] != "secret-notion-token"
    assert Fernet(settings.token_encryption_key.encode()).decrypt(row["access_token"].encode()) == b"secret-notion-token"
    assert client.get(f"/connect/status/{state}").json()["status"] == "done"


def test_finish_jira_saves_site(client_for, fake_broker, pg_dsn):
    client = client_for(db=True)
    state = _start(client, fake_broker, "jira", JIRA_RESULT)
    r = client.get("/connect/finish", params=fin(state))
    assert r.status_code == 200 and "Connected to Jira" in r.text
    assert "secret-jira" not in r.text
    (row,) = _rows(pg_dsn, client.org_id, "jira")
    assert row["workspace_id"] == "cloud-123"
    assert row["site_url"] == "https://acme.atlassian.net"
    assert row["workspace_name"] == "acme"
    f = Fernet(settings.token_encryption_key.encode())
    assert f.decrypt(row["access_token"].encode()) == b"secret-jira-token"
    assert f.decrypt(row["refresh_token"].encode()) == b"secret-jira-refresh"
    expected = datetime.now(timezone.utc) + timedelta(seconds=3600)
    assert abs((row["expires_at"] - expected).total_seconds()) < 60
    assert client.get(f"/connect/status/{state}").json()["status"] == "done"


def test_connections_reports_needs_reconnect_until_reconnected(make_client, fake_broker, pg_dsn):
    import uuid

    from app.api.routes import connections as connections_routes
    from app.integrations import jira_auth

    client = make_client(
        routers=[(connect_routes.router, "/connect"), (connections_routes.router, "/connections")],
        base_url=f"http://127.0.0.1:{PORT}",
        db=True,
    )
    state = _start(client, fake_broker, "jira", JIRA_RESULT)
    assert "Connected to Jira" in client.get("/connect/finish", params=fin(state)).text
    (row,) = _rows(pg_dsn, client.org_id, "jira")

    def jira_status():
        (status,) = [c for c in client.get("/connections").json() if c["provider"] == "jira"]
        return status

    assert jira_status()["needs_reconnect"] is False
    jira_auth._NEEDS_RECONNECT.add(uuid.UUID(str(row["id"])))  # what a refused refresh records
    assert jira_status()["needs_reconnect"] is True
    # Reconnecting (a fresh Connect flow) clears it.
    state = _start(client, fake_broker, "jira", JIRA_RESULT)
    assert "Connected to Jira" in client.get("/connect/finish", params=fin(state)).text
    assert jira_status()["needs_reconnect"] is False


def test_finish_denied(client_for, fake_broker, pg_dsn):
    client = client_for(db=True)
    state = _start(client, fake_broker, "notion", {"provider": "notion", "error": "Notion access wasn't granted."})
    r = client.get("/connect/finish", params=fin(state))
    assert r.status_code == 200
    assert "Notion access wasn&#x27;t granted." in r.text or "Notion access wasn&#39;t granted." in r.text
    assert client.get(f"/connect/status/{state}").json() == {
        "status": "error",
        "message": "Notion access wasn't granted.",
    }
    assert _rows(pg_dsn, client.org_id, "notion") == []


def test_finish_escapes_error_text(client_for, fake_broker, pg_dsn):
    client = client_for(db=True)
    state = _start(client, fake_broker, "notion", {"provider": "notion", "error": "<b>nope</b>"})
    r = client.get("/connect/finish", params=fin(state))
    assert "<b>nope</b>" not in r.text and "&lt;b&gt;nope&lt;/b&gt;" in r.text


def test_finish_unknown_state_page(client_for, fake_broker, pg_dsn):
    r = client_for(db=True).get("/connect/finish", params={"state": "u" * 43})
    assert r.status_code == 200
    assert "This sign-in link has expired. Start again from the app." in r.text
    assert r.headers["cache-control"] == "no-store"
    assert fake_broker.redeemed == []


def test_finish_missing_state_page(client_for, fake_broker, pg_dsn):
    r = client_for(db=True).get("/connect/finish")
    assert r.status_code == 200 and "has expired" in r.text


def test_finish_without_pickup_is_expired_and_keeps_flow(client_for, fake_broker, pg_dsn):
    client = client_for(db=True)
    state = _start(client, fake_broker, "notion", NOTION_RESULT)
    for params in ({"state": state}, {"state": state, "pickup": ""}):
        r = client.get("/connect/finish", params=params)
        assert r.status_code == 200 and "has expired" in r.text
    # Nothing was sent to the Worker and the real redirect still completes the sign-in.
    assert fake_broker.redeemed == []
    assert client.get(f"/connect/status/{state}").json()["status"] == "pending"
    assert "Connected to Notion" in client.get("/connect/finish", params=fin(state)).text


def test_finish_passes_pickup_to_redeem(client_for, fake_broker, pg_dsn):
    client = client_for(db=True)
    state = _start(client, fake_broker, "notion", NOTION_RESULT)
    client.get("/connect/finish", params=fin(state))
    (body,) = fake_broker.redeemed
    assert body["state"] == state and body["pickup"] == pickup_for(state)
    assert body["verifier"] == connect_routes._FLOWS[state].verifier


def test_finish_wrong_pickup_refused(client_for, fake_broker, pg_dsn):
    client = client_for(db=True)
    state = _start(client, fake_broker, "notion", NOTION_RESULT)
    r = client.get("/connect/finish", params={"state": state, "pickup": "not-the-pickup"})
    assert "has expired" in r.text
    assert client.get(f"/connect/status/{state}").json() == {"status": "error", "message": EXPIRED}
    assert _rows(pg_dsn, client.org_id, "notion") == []


def test_finish_twice(client_for, fake_broker, pg_dsn):
    client = client_for(db=True)
    state = _start(client, fake_broker, "notion", NOTION_RESULT)
    assert "Connected to Notion" in client.get("/connect/finish", params=fin(state)).text
    second = client.get("/connect/finish", params=fin(state))
    assert second.status_code == 200 and "has expired" in second.text
    assert len(_rows(pg_dsn, client.org_id, "notion")) == 1
    # The first, successful outcome is what /status keeps reporting.
    assert client.get(f"/connect/status/{state}").json()["status"] == "done"


def test_finish_broker_unreachable(client_for, fake_broker, pg_dsn):
    client = client_for(db=True)
    state = _start(client, fake_broker, "notion", NOTION_RESULT)
    fake_broker.unreachable = True
    r = client.get("/connect/finish", params=fin(state))
    assert r.status_code == 200
    assert "Couldn&#x27;t reach the sign-in service" in r.text or "Couldn&#39;t reach the sign-in service" in r.text
    assert client.get(f"/connect/status/{state}").json() == {"status": "error", "message": UNREACHABLE}
    assert _rows(pg_dsn, client.org_id, "notion") == []


def test_finish_redeem_expired_at_broker(client_for, fake_broker, pg_dsn):
    client = client_for(db=True)
    state = _start(client, fake_broker, "notion", NOTION_RESULT)
    fake_broker.results.clear()  # the Worker's 120 s result TTL ran out
    r = client.get("/connect/finish", params=fin(state))
    assert "has expired" in r.text
    assert client.get(f"/connect/status/{state}").json() == {"status": "error", "message": EXPIRED}


def test_finish_provider_mismatch_is_refused(client_for, fake_broker, pg_dsn):
    client = client_for(db=True)
    state = _start(client, fake_broker, "notion", JIRA_RESULT)
    client.get("/connect/finish", params=fin(state))
    assert client.get(f"/connect/status/{state}").json()["status"] == "error"
    assert _rows(pg_dsn, client.org_id, "jira") == [] and _rows(pg_dsn, client.org_id, "notion") == []


def test_two_flows_same_provider(client_for, fake_broker, pg_dsn):
    client = client_for(db=True)
    first = _start(client, fake_broker, "notion", {**NOTION_RESULT, "workspace_name": "First"})
    second = _start(client, fake_broker, "notion", {**NOTION_RESULT, "workspace_name": "Second"})
    assert first != second
    assert "Connected to Notion" in client.get("/connect/finish", params=fin(second)).text
    rows = _rows(pg_dsn, client.org_id, "notion")
    assert [r["workspace_name"] for r in rows] == ["Second"]
    assert client.get(f"/connect/status/{first}").json()["status"] == "pending"


def test_status_and_cancel(client_for, fake_broker):
    client = client_for()
    state = client.post("/connect/notion/start").json()["state"]
    assert client.get(f"/connect/status/{state}").json() == {"status": "pending", "message": None}
    assert client_for(role="member").get(f"/connect/status/{state}").status_code == 403


def test_cancel_forgets_flow(client_for, fake_broker, pg_dsn):
    client = client_for(db=True)
    state = client.post("/connect/notion/start").json()["state"]
    r = client.post(f"/connect/{state}/cancel")
    assert r.status_code == 200 and r.json() == {}
    assert client.get(f"/connect/status/{state}").status_code == 404
    assert "has expired" in client.get("/connect/finish", params=fin(state)).text
    assert fake_broker.redeemed == []


def test_flows_expire_after_ten_minutes(client_for, fake_broker):
    client = client_for()
    state = client.post("/connect/notion/start").json()["state"]
    connect_routes._FLOWS[state].created -= 601
    assert client.get(f"/connect/status/{state}").status_code == 404
    assert state not in connect_routes._FLOWS


def test_identity_handler_called_for_google(client_for, fake_broker, pg_dsn, monkeypatch):
    calls = []

    async def handler(session, user_id, result):
        calls.append((user_id, result))
        return "Ada <Lovelace>"

    monkeypatch.setattr(connect_routes, "IDENTITY_HANDLER", handler)
    client = client_for(db=True)
    result = {"provider": "google", "provider_user_id": "g-1", "email": "ada@example.com", "name": "Ada"}
    state = _start(client, fake_broker, "google", result)
    r = client.get("/connect/finish", params=fin(state))
    assert "Signed in as Ada &lt;Lovelace&gt;" in r.text
    assert calls == [(client.user_id, result)]
    assert client.get(f"/connect/status/{state}").json()["status"] == "done"


def test_identity_handler_refusal(client_for, fake_broker, pg_dsn, monkeypatch):
    async def handler(session, user_id, result):
        raise ValueError("That GitHub account is linked to someone else.")

    monkeypatch.setattr(connect_routes, "IDENTITY_HANDLER", handler)
    client = client_for(db=True)
    state = _start(client, fake_broker, "github", {"provider": "github", "provider_user_id": "1", "email": None, "name": "x"})
    r = client.get("/connect/finish", params=fin(state))
    assert "linked to someone else" in r.text
    assert client.get(f"/connect/status/{state}").json() == {
        "status": "error",
        "message": "That GitHub account is linked to someone else.",
    }


class _FakeRedis:
    def __init__(self, data):
        self.data = dict(data)

    async def get(self, key):
        return self.data.get(key)

    async def delete(self, key):
        self.data.pop(key, None)


def test_server_callbacks_unchanged(make_client, external, pg_dsn, monkeypatch):
    """The server-mode Notion callback still saves through the moved function."""
    client = make_client(mode="server", db=True, routers=[(notion_oauth.router, "/oauth/notion")])
    fake = _FakeRedis({"oauth_state:notion:st": str(client.org_id)})
    monkeypatch.setattr(notion_oauth, "get_redis", lambda: fake)

    def token_endpoint(request):
        assert str(request.url) == notion_oauth.NOTION_TOKEN_URL
        return httpx.Response(200, json={k: v for k, v in NOTION_RESULT.items() if k != "provider"})

    external.handler = token_endpoint
    r = client.get("/oauth/notion/callback", params={"code": "c", "state": "st"}, follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"].endswith("/connections?connected=notion")
    (row,) = _rows(pg_dsn, client.org_id, "notion")
    assert row["workspace_name"] == "Acme Notes"
    assert Fernet(settings.token_encryption_key.encode()).decrypt(row["access_token"].encode()) == b"secret-notion-token"
    assert fake.data == {}


def test_finish_page_shows_logo_when_the_official_file_exists(client_for, fake_broker, pg_dsn, tmp_path, monkeypatch):
    static = tmp_path / "ui"
    (static / "brand").mkdir(parents=True)
    monkeypatch.setenv("DESKTOP_STATIC_DIR", str(static))
    client = client_for(db=True)
    state = _start(client, fake_broker, "notion", NOTION_RESULT)
    page = client.get("/connect/finish", params=fin(state)).text
    assert '<span class="name">Notion</span>' in page and "<img" not in page  # no file yet: the name stands in
    (static / "brand" / "notion.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    state = _start(client, fake_broker, "notion", NOTION_RESULT)
    page = client.get("/connect/finish", params=fin(state)).text
    assert '<img src="/brand/notion.svg" alt="Notion">' in page


def test_finish_page_fonts_are_served_and_nothing_else(client_for):
    client = client_for(db=True)
    r = client.get("/connect/assets/InstrumentSerif-Regular.ttf")
    assert r.status_code == 200 and r.headers["content-type"] == "font/ttf" and len(r.content) > 10_000
    for name in ("README.md", "GeistMono-OFL.txt", "..%2Fconnect_page.py", "nope.ttf"):
        assert client.get(f"/connect/assets/{name}").status_code == 404


def test_finish_assets_404_in_server_mode(client_for):
    assert client_for(mode="server").get("/connect/assets/InstrumentSerif-Regular.ttf").status_code == 404

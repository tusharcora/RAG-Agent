"""Connecting GitHub as a knowledge source: the desktop Connect purpose
`github_repos` (through the fake sign-in Worker), the web OAuth routes, and
`save_github_connection`'s reconnect rules."""

import json
import uuid
from urllib.parse import urlencode
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from cryptography.fernet import Fernet

from app.api.routes import github_source_oauth
from app.core.auth import require_auth
from app.core.config import settings
from app.integrations import github_api

from .conftest import db_run
from .test_connect import _rows, _start, client_for, fake_broker, fin  # noqa: F401 (fixtures)

API = "https://api.github.test"

RESULT = {
    "provider": "github_repos",
    "access_token": "ghu_secret_access",
    "refresh_token": "ghr_secret_refresh",
    "expires_in": 28800,
    "refresh_token_expires_in": 15811200,
    "user_id": 42,
    "login": "octo",
    "app_slug": "ka-test-app",
    "installations": [
        {"id": 11, "account_login": "octo", "account_type": "User"},
        {"id": 12, "account_login": "acme", "account_type": "Organization"},
    ],
}


@pytest.fixture
def gh(monkeypatch):
    """Fake GitHub REST API; `gh.totals` maps installation id -> total_count (missing -> 500)."""

    class Fake:
        totals: dict[int, int] = {}
        calls: list[httpx.Request] = []

    fake = Fake()
    fake.totals, fake.calls = {11: 3, 12: 4}, []

    def handler(request: httpx.Request) -> httpx.Response:
        fake.calls.append(request)
        assert request.url.host == "api.github.test"
        parts = request.url.path.strip("/").split("/")
        if parts[:2] == ["user", "installations"] and len(parts) == 4 and parts[3] == "repositories":
            total = fake.totals.get(int(parts[2]))
            if total is None:
                return httpx.Response(500, json={"message": "boom"})
            return httpx.Response(200, json={"total_count": total, "repositories": []})
        return httpx.Response(404, json={})

    monkeypatch.setattr(github_api, "TRANSPORT", httpx.MockTransport(handler))
    monkeypatch.setattr(settings, "github_api_url", API)
    return fake


def _connect(client, fb, result=RESULT):
    state = _start(client, fb, "github_repos", result)
    return client.get("/connect/finish", params=fin(state)), state


def test_connect_github_repos_start_and_finish_saves_connection(client_for, fake_broker, gh, pg_dsn, monkeypatch):
    monkeypatch.setattr(settings, "token_encryption_key", Fernet.generate_key().decode())
    client = client_for(db=True)
    r, state = _connect(client, fake_broker)
    assert r.status_code == 200 and "Connected to GitHub" in r.text
    assert "7 repositories" in r.text and "octo" in r.text
    assert "ghu_secret" not in r.text and "ghr_secret" not in r.text
    (row,) = _rows(pg_dsn, client.org_id, "github")
    assert row["workspace_id"] == "42" and row["workspace_name"] == "octo"
    assert row["site_url"] == "https://github.com/octo" and row["bot_id"] == "ka-test-app"
    f = Fernet(settings.token_encryption_key.encode())
    assert f.decrypt(row["access_token"].encode()) == b"ghu_secret_access"
    assert f.decrypt(row["refresh_token"].encode()) == b"ghr_secret_refresh"
    expected = datetime.now(timezone.utc) + timedelta(seconds=28800)
    assert abs((row["expires_at"] - expected).total_seconds()) < 60
    assert client.get(f"/connect/status/{state}").json()["status"] == "done"
    # The count calls use the user's token against the configured API base.
    assert sorted(c.url.path for c in gh.calls) == [
        "/user/installations/11/repositories",
        "/user/installations/12/repositories",
    ]
    assert all(c.headers["authorization"] == "Bearer ghu_secret_access" for c in gh.calls)
    assert all(c.url.params["per_page"] == "1" for c in gh.calls)


def test_start_github_repos_is_a_purpose(client_for, fake_broker):
    assert client_for().post("/connect/github_repos/start").status_code == 200


def test_finish_uses_the_github_logo_tile(client_for, fake_broker, gh, pg_dsn, tmp_path, monkeypatch):
    static = tmp_path / "ui"
    (static / "brand").mkdir(parents=True)
    (static / "brand" / "github.svg").write_text("<svg/>")
    monkeypatch.setenv("DESKTOP_STATIC_DIR", str(static))
    monkeypatch.setattr(settings, "token_encryption_key", Fernet.generate_key().decode())
    r, _ = _connect(client_for(db=True), fake_broker)
    assert 'src="/brand/github.svg"' in r.text


def test_finish_github_no_installations(client_for, fake_broker, gh, pg_dsn, monkeypatch):
    monkeypatch.setattr(settings, "token_encryption_key", Fernet.generate_key().decode())
    client = client_for(db=True)
    r, _ = _connect(client, fake_broker, {**RESULT, "installations": [], "login": "<b>octo</b>"})
    assert "Connected to GitHub" in r.text
    assert "No repositories yet" in r.text
    assert "After choosing them on GitHub, come back here and press Sync now." in r.text
    assert 'href="https://github.com/apps/ka-test-app/installations/new"' in r.text
    assert "<b>octo</b>" not in r.text and "&lt;b&gt;octo&lt;/b&gt;" in r.text
    assert gh.calls == []
    assert len(_rows(pg_dsn, client.org_id, "github")) == 1


def test_finish_github_slug_is_escaped_in_the_link(client_for, fake_broker, gh, pg_dsn, monkeypatch):
    monkeypatch.setattr(settings, "token_encryption_key", Fernet.generate_key().decode())
    r, _ = _connect(client_for(db=True), fake_broker, {**RESULT, "installations": [], "app_slug": 'x"><script>'})
    assert "<script>" not in r.text


def test_finish_github_count_failure_omits_subtitle_but_connects(client_for, fake_broker, gh, pg_dsn, monkeypatch):
    monkeypatch.setattr(settings, "token_encryption_key", Fernet.generate_key().decode())
    gh.totals = {11: 3}  # installation 12 answers 500
    client = client_for(db=True)
    r, state = _connect(client, fake_broker)
    assert "Connected to GitHub" in r.text and "octo" in r.text
    assert "repositories" not in r.text and "No repositories yet" not in r.text
    assert client.get(f"/connect/status/{state}").json()["status"] == "done"
    assert len(_rows(pg_dsn, client.org_id, "github")) == 1


def test_finish_github_denied_shows_not_granted(client_for, fake_broker, gh, pg_dsn):
    client = client_for(db=True)
    msg = "GitHub access wasn't granted."
    state = _start(client, fake_broker, "github_repos", {"provider": "github_repos", "error": msg})
    r = client.get("/connect/finish", params=fin(state))
    assert "GitHub access wasn&#x27;t granted." in r.text or "GitHub access wasn&#39;t granted." in r.text
    assert client.get(f"/connect/status/{state}").json() == {"status": "error", "message": msg}
    assert _rows(pg_dsn, client.org_id, "github") == []


def _seed_synced(dsn, connection_id):
    doc_id = uuid.uuid4()
    db_run(
        dsn,
        "INSERT INTO documents (id, source, external_id, connection_id, title, url)"
        " VALUES ($1, 'github'::rag_source, 'file:1:README.md', $2, 'r', 'https://x')",
        doc_id,
        connection_id,
    )
    db_run(
        dsn,
        "INSERT INTO github_repos (connection_id, installation_id, repo_id, full_name, html_url, default_branch)"
        " VALUES ($1, 11, 1, 'octo/r', 'https://github.com/octo/r', 'main')",
        connection_id,
    )
    db_run(dsn, "INSERT INTO github_failures (connection_id, external_id, version) VALUES ($1, 'file:1:x', 'v')", connection_id)


def _counts(dsn, connection_id):
    return (
        len(db_run(dsn, "SELECT 1 FROM documents WHERE connection_id = $1", connection_id)),
        len(db_run(dsn, "SELECT 1 FROM github_repos WHERE connection_id = $1", connection_id)),
        len(db_run(dsn, "SELECT 1 FROM github_failures WHERE connection_id = $1", connection_id)),
    )


def test_reconnect_same_user_keeps_documents(client_for, fake_broker, gh, pg_dsn, monkeypatch):
    monkeypatch.setattr(settings, "token_encryption_key", Fernet.generate_key().decode())
    client = client_for(db=True)
    _connect(client, fake_broker)
    (row,) = _rows(pg_dsn, client.org_id, "github")
    _seed_synced(pg_dsn, row["id"])
    db_run(pg_dsn, "UPDATE oauth_connections SET needs_reconnect_at = now() WHERE id = $1", row["id"])
    _connect(client, fake_broker, {**RESULT, "access_token": "ghu_new"})
    (again,) = _rows(pg_dsn, client.org_id, "github")
    assert again["id"] == row["id"] and again["needs_reconnect_at"] is None and again["disconnected_at"] is None
    assert Fernet(settings.token_encryption_key.encode()).decrypt(again["access_token"].encode()) == b"ghu_new"
    assert _counts(pg_dsn, row["id"]) == (1, 1, 1)


def test_reconnect_after_disconnect_resets_sync_state(client_for, fake_broker, gh, pg_dsn, monkeypatch):
    # A sync still running when Disconnect landed can write repo cursors after Disconnect reset them;
    # reconnecting starts the cursors over (documents of the same user are kept).
    monkeypatch.setattr(settings, "token_encryption_key", Fernet.generate_key().decode())
    client = client_for(db=True)
    _connect(client, fake_broker)
    (row,) = _rows(pg_dsn, client.org_id, "github")
    _seed_synced(pg_dsn, row["id"])
    db_run(pg_dsn, "UPDATE oauth_connections SET needs_reconnect_at = now(), disconnected_at = now() WHERE id = $1", row["id"])
    _connect(client, fake_broker, {**RESULT, "access_token": "ghu_new"})
    (again,) = _rows(pg_dsn, client.org_id, "github")
    assert again["id"] == row["id"] and again["needs_reconnect_at"] is None and again["disconnected_at"] is None
    assert _counts(pg_dsn, row["id"]) == (1, 0, 0)


def test_reconnect_other_user_deletes_documents_and_state(client_for, fake_broker, gh, pg_dsn, monkeypatch):
    monkeypatch.setattr(settings, "token_encryption_key", Fernet.generate_key().decode())
    client = client_for(db=True)
    _connect(client, fake_broker)
    (row,) = _rows(pg_dsn, client.org_id, "github")
    _seed_synced(pg_dsn, row["id"])
    _connect(client, fake_broker, {**RESULT, "user_id": 99, "login": "someone-else"})
    (again,) = _rows(pg_dsn, client.org_id, "github")
    assert again["id"] == row["id"] and again["workspace_id"] == "99" and again["workspace_name"] == "someone-else"
    assert _counts(pg_dsn, row["id"]) == (0, 0, 0)


# --- web OAuth -------------------------------------------------------------


class FakeRedis:
    def __init__(self, data=None):
        self.data = dict(data or {})
        self.ttls = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        self.data[key] = value
        self.ttls[key] = ex

    async def delete(self, key):
        self.data.pop(key, None)


def _state(web, name="st", org_id=None, user_id=None):
    """The Redis CSRF state /authorize stores: the initiating org and user."""
    web.redis.data[f"oauth_state:github_source:{name}"] = json.dumps(
        {"org_id": str(org_id or web.org_id), "user_id": str(user_id or web.user_id)}
    )


@pytest.fixture
def web(make_client, monkeypatch):
    monkeypatch.setattr(settings, "token_encryption_key", Fernet.generate_key().decode())
    for name, value in {
        "github_source_client_id": "Iv1.cid",
        "github_source_client_secret": "csecret",
        "github_source_redirect_uri": "https://app.example/oauth/github_source/callback",
        "github_source_app_slug": "web-slug",
        "github_api_url": API,
    }.items():
        monkeypatch.setattr(settings, name, value)
    redis = FakeRedis()
    monkeypatch.setattr(github_source_oauth, "get_redis", lambda: redis)
    client = make_client(mode="server", db=True, routers=[(github_source_oauth.router, "/oauth/github_source")])
    client.redis = redis
    client.exchanges = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "github.com" and request.url.path == "/login/oauth/access_token":
            assert request.headers["accept"] == "application/json"
            client.exchanges.append(dict(httpx.QueryParams(request.content.decode())))
            code = client.exchanges[-1]["code"]
            if code == "denied":
                return httpx.Response(200, json={"error": "bad_verification_code"})
            if code == "broken":
                return httpx.Response(502, text="<html>bad gateway</html>")
            return httpx.Response(
                200,
                json={
                    "access_token": "ghu_web_access",
                    "refresh_token": "ghr_web_refresh",
                    "expires_in": 28800,
                    "refresh_token_expires_in": 15811200,
                },
            )
        if request.url.host == "api.github.test" and request.url.path == "/user":
            assert request.headers["authorization"] == "Bearer ghu_web_access"
            return httpx.Response(200, json={"id": 77, "login": "webby", "email": "private@example.com"})
        return httpx.Response(404, json={})

    monkeypatch.setattr(github_api, "TRANSPORT", httpx.MockTransport(handler))
    return client


def test_web_authorize_requires_auth_and_redirects(web):
    r = web.get("/oauth/github_source/authorize", follow_redirects=False)
    assert r.status_code in (302, 307)
    loc = httpx.URL(r.headers["location"])
    assert (loc.host, loc.path) == ("github.com", "/login/oauth/authorize")
    q = dict(loc.params)
    assert q["client_id"] == "Iv1.cid" and q["redirect_uri"] == settings.github_source_redirect_uri
    key = f"oauth_state:github_source:{q['state']}"
    assert json.loads(web.redis.data[key]) == {"org_id": str(web.org_id), "user_id": str(web.user_id)}
    assert web.redis.ttls[key] == settings.oauth_state_ttl_seconds

    web.app.dependency_overrides.pop(require_auth)
    assert web.get("/oauth/github_source/authorize", follow_redirects=False).status_code == 401


def test_web_authorize_needs_owner_or_admin(web, make_client):
    from app.core.auth import AuthContext

    web.app.dependency_overrides[require_auth] = lambda: AuthContext(
        org_id=web.org_id, role="member", user_id=web.user_id
    )
    assert web.get("/oauth/github_source/authorize", follow_redirects=False).status_code == 403
    assert web.redis.data == {}


def test_web_callback_bad_state_400(web):
    r = web.get("/oauth/github_source/callback", params={"code": "c", "state": "nope"}, follow_redirects=False)
    assert r.status_code == 400
    assert web.exchanges == []


def test_web_callback_saves_and_redirects(web, pg_dsn):
    _state(web)
    r = web.get("/oauth/github_source/callback", params={"code": "good", "state": "st"}, follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"] == f"{settings.frontend_url}/connections?connected=github"
    assert web.redis.data == {}
    assert web.exchanges[0]["code"] == "good" and web.exchanges[0]["client_secret"] == "csecret"
    assert web.exchanges[0]["redirect_uri"] == settings.github_source_redirect_uri
    (row,) = _rows(pg_dsn, web.org_id, "github")
    assert row["workspace_id"] == "77" and row["workspace_name"] == "webby" and row["bot_id"] == "web-slug"
    assert row["site_url"] == "https://github.com/webby"
    f = Fernet(settings.token_encryption_key.encode())
    assert f.decrypt(row["access_token"].encode()) == b"ghu_web_access"
    assert f.decrypt(row["refresh_token"].encode()) == b"ghr_web_refresh"


def _error_redirect(message):
    return f"{settings.frontend_url}/connections?" + urlencode({"error": message})


def test_web_callback_denied_redirects_with_error_and_saves_nothing(web, pg_dsn):
    _state(web)
    r = web.get("/oauth/github_source/callback", params={"code": "denied", "state": "st"}, follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"] == _error_redirect("GitHub access wasn't granted.")
    assert _rows(pg_dsn, web.org_id, "github") == []
    # GitHub's own "user said no" redirect carries error instead of code.
    _state(web, "st2")
    r = web.get("/oauth/github_source/callback", params={"error": "access_denied", "state": "st2"}, follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"] == _error_redirect("GitHub access wasn't granted.")
    assert web.exchanges[-1]["code"] == "denied"  # the second one never reached the exchange
    assert len(web.exchanges) == 1


def test_web_callback_github_failure_redirects_with_error(web, pg_dsn):
    _state(web)
    r = web.get("/oauth/github_source/callback", params={"code": "broken", "state": "st"}, follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"] == _error_redirect("Couldn't reach GitHub.")
    assert _rows(pg_dsn, web.org_id, "github") == []


def test_web_callback_other_user_is_400_and_saves_nothing(web, pg_dsn):
    # A state started by someone else (e.g. a link an attacker sends) can't attach their GitHub
    # account to the signed-in person's workspace.
    _state(web, user_id=uuid.uuid4())
    r = web.get("/oauth/github_source/callback", params={"code": "good", "state": "st"}, follow_redirects=False)
    assert r.status_code == 400
    assert web.exchanges == [] and _rows(pg_dsn, web.org_id, "github") == []
    assert web.redis.data == {}  # used up either way


def test_web_callback_other_org_is_400_and_saves_nothing(web, pg_dsn):
    other_org = uuid.uuid4()
    _state(web, org_id=other_org)
    r = web.get("/oauth/github_source/callback", params={"code": "good", "state": "st"}, follow_redirects=False)
    assert r.status_code == 400
    assert web.exchanges == []
    assert _rows(pg_dsn, web.org_id, "github") == [] and _rows(pg_dsn, other_org, "github") == []


def test_web_callback_requires_auth(web, pg_dsn):
    _state(web)
    web.app.dependency_overrides.pop(require_auth)
    r = web.get("/oauth/github_source/callback", params={"code": "good", "state": "st"}, follow_redirects=False)
    assert r.status_code == 401
    assert web.exchanges == [] and _rows(pg_dsn, web.org_id, "github") == []


def test_github_source_routes_404_without_config(web, monkeypatch):
    monkeypatch.setattr(settings, "github_source_client_id", "")
    _state(web)
    assert web.get("/oauth/github_source/authorize", follow_redirects=False).status_code == 404
    r = web.get("/oauth/github_source/callback", params={"code": "c", "state": "st"}, follow_redirects=False)
    assert r.status_code == 404


# --- GET /connections github block ------------------------------------------


def test_connections_reports_github_repos(make_client, pg_dsn):
    from app.api.routes import connections as connections_routes

    client = make_client(db=True, routers=[(connections_routes.router, "/connections")])

    def github():
        (s,) = [c for c in client.get("/connections").json() if c["provider"] == "github"]
        return s

    assert github()["github"] is None  # not connected

    conn_id = uuid.uuid4()
    db_run(
        pg_dsn,
        "INSERT INTO oauth_connections (id, org_id, provider, workspace_id, workspace_name, bot_id, access_token)"
        " VALUES ($1, $2, 'github', '42', 'octo', 'ka app/x', 'x')",
        conn_id,
        client.org_id,
    )
    assert github()["github"] == {
        "repo_count": 0,
        "repo_names": [],
        "choose_url": "https://github.com/apps/ka%20app%2Fx/installations/new",
    }
    for i, name in ((2, "octo/b"), (1, "octo/a")):
        db_run(
            pg_dsn,
            "INSERT INTO github_repos (connection_id, installation_id, repo_id, full_name, html_url, default_branch)"
            " VALUES ($1, 11, $2, $3, 'https://github.com/x', 'main')",
            conn_id,
            i,
            name,
        )
    g = github()["github"]
    assert g["repo_count"] == 2 and g["repo_names"] == ["octo/a", "octo/b"]

    db_run(pg_dsn, "UPDATE oauth_connections SET bot_id = NULL WHERE id = $1", conn_id)
    assert github()["github"]["choose_url"] == ""
    others = [c for c in client.get("/connections").json() if c["provider"] != "github"]
    assert all(c["github"] is None for c in others)

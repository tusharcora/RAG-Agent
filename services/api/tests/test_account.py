"""Optional app sign-in (identity only) on the desktop: /account/*, the
Connect flow's google/github finish, and /auth/local's stable lookup."""

import json
import uuid

import pytest

from app.api.routes import account as account_routes
from app.api.routes import auth as auth_routes
from app.api.routes import connect as connect_routes
from app.core.auth import AuthContext, require_auth
from app.core.config import settings

from .conftest import db_run
from .test_connect import BROKER, PORT, _start, fake_broker  # noqa: F401 - fixture

LOCAL_EMAIL = "you@this-computer.local"
COLLISION = "That account is already linked to another profile on this computer."


def _uid() -> str:
    return uuid.uuid4().hex[:12]


def _identity(provider: str, **over) -> dict:
    tag = _uid()
    return {
        "provider": provider,
        "provider_user_id": f"{provider}-{tag}",
        "email": f"ada-{tag}@example.com",
        "name": "Ada Lovelace",
        **over,
    }


@pytest.fixture
def client(make_client, fake_broker, monkeypatch):  # noqa: F811
    """Desktop app with /connect, /account and /auth, the real identity
    handler registered, backed by the pgserver database."""
    monkeypatch.setattr(connect_routes, "IDENTITY_HANDLER", account_routes.link_identity)

    def _make(**kw):
        return make_client(
            routers=[
                (connect_routes.router, "/connect"),
                (account_routes.router, "/account"),
                (auth_routes.router, "/auth"),
            ],
            base_url=f"http://127.0.0.1:{PORT}",
            **kw,
        )

    return _make


def _sign_in(client, fb, result) -> str:
    state = _start(client, fb, result["provider"], result)
    return client.get("/connect/finish", params={"state": state}).text


def _user(dsn, user_id):
    (row,) = db_run(dsn, "SELECT email, display_name FROM users WHERE id = $1", user_id)
    return dict(row)


def _identities(dsn, user_id):
    return [dict(r) for r in db_run(dsn, "SELECT provider, provider_user_id, email FROM oauth_identities WHERE user_id = $1", user_id)]


def _drop_local_user(dsn):
    db_run(dsn, "DELETE FROM users WHERE email = $1", LOCAL_EMAIL)


def _as(client, user_id, org_id):
    client.app.dependency_overrides[require_auth] = lambda: AuthContext(org_id=org_id, role="owner", user_id=user_id)


def test_importing_account_registers_identity_handler():
    # Registered at import (main.py imports account); the fake_broker fixture
    # resets it for test_connect, so check without that fixture.
    assert connect_routes.IDENTITY_HANDLER is account_routes.link_identity


def test_finish_google_links_identity_and_updates_user(client, fake_broker, pg_dsn):  # noqa: F811
    c = client(db=True)
    result = _identity("google")
    page = _sign_in(c, fake_broker, result)
    assert "Signed in as Ada Lovelace" in page
    assert _user(pg_dsn, c.user_id) == {"email": result["email"], "display_name": "Ada Lovelace"}
    assert _identities(pg_dsn, c.user_id) == [
        {"provider": "google", "provider_user_id": result["provider_user_id"], "email": result["email"]}
    ]
    # Signing in again with the same account updates rather than duplicates.
    again = {**result, "name": "Ada King"}
    assert "Signed in as Ada King" in _sign_in(c, fake_broker, again)
    assert len(_identities(pg_dsn, c.user_id)) == 1
    assert _user(pg_dsn, c.user_id)["display_name"] == "Ada King"


def test_google_start_works_once_registered(client, fake_broker):  # noqa: F811
    r = client().post("/connect/google/start")
    assert r.status_code == 200 and r.json()["url"].startswith(f"{BROKER}/start/google?")
    assert client().post("/connect/github/start").status_code == 200


def test_github_replaces_google_link(client, fake_broker, pg_dsn):  # noqa: F811
    c = client(db=True)
    _sign_in(c, fake_broker, _identity("google"))
    gh = _identity("github", name="ada-gh")
    assert "Signed in as ada-gh" in _sign_in(c, fake_broker, gh)
    assert _identities(pg_dsn, c.user_id) == [
        {"provider": "github", "provider_user_id": gh["provider_user_id"], "email": gh["email"]}
    ]
    assert _user(pg_dsn, c.user_id) == {"email": gh["email"], "display_name": "ada-gh"}


def test_identity_without_email_keeps_local_email(client, fake_broker, pg_dsn):  # noqa: F811
    c = client(db=True)
    before = _user(pg_dsn, c.user_id)["email"]
    page = _sign_in(c, fake_broker, _identity("github", email=None, name="octo"))
    assert "Signed in as octo" in page
    assert _user(pg_dsn, c.user_id) == {"email": before, "display_name": "octo"}
    status = c.get("/account").json()
    assert status["signed_in"] is True and status["provider"] == "github"
    assert status["email"] is None and status["name"] == "octo"


def test_identity_empty_name_falls_back(client, fake_broker, pg_dsn):  # noqa: F811
    c = client(db=True)
    result = _identity("google", name="  ")
    local_part = result["email"].split("@")[0]
    assert f"Signed in as {local_part}" in _sign_in(c, fake_broker, result)
    assert _user(pg_dsn, c.user_id)["display_name"] == local_part
    assert "Signed in as You" in _sign_in(c, fake_broker, _identity("github", name="", email=None))
    assert _user(pg_dsn, c.user_id)["display_name"] == "You"


def test_email_collision_is_refused(client, fake_broker, pg_dsn):  # noqa: F811
    c = client(db=True)
    other_email = f"taken-{_uid()}@example.com"
    db_run(pg_dsn, "INSERT INTO users (id, email) VALUES ($1, $2)", uuid.uuid4(), other_email)
    before = _user(pg_dsn, c.user_id)
    state = _start(c, fake_broker, "google", _identity("google", email=other_email.upper()))
    page = c.get("/connect/finish", params={"state": state}).text
    assert "already linked to another profile on this computer" in page
    assert c.get(f"/connect/status/{state}").json() == {"status": "error", "message": COLLISION}
    assert _user(pg_dsn, c.user_id) == before
    assert _identities(pg_dsn, c.user_id) == []


def test_identity_linked_to_other_user_is_refused(client, fake_broker, pg_dsn):  # noqa: F811
    c = client(db=True)
    other = uuid.uuid4()
    db_run(pg_dsn, "INSERT INTO users (id, email) VALUES ($1, $2)", other, f"o-{_uid()}@t.io")
    result = _identity("github")
    db_run(
        pg_dsn,
        "INSERT INTO oauth_identities (id, provider, provider_user_id, user_id) VALUES ($1, 'github', $2, $3)",
        uuid.uuid4(), result["provider_user_id"], other,
    )
    assert "already linked to another profile" in _sign_in(c, fake_broker, result)
    assert _identities(pg_dsn, c.user_id) == []
    assert _identities(pg_dsn, other)[0]["provider_user_id"] == result["provider_user_id"]


def test_sign_out_restores_local_profile_keeps_data(client, fake_broker, pg_dsn):  # noqa: F811
    _drop_local_user(pg_dsn)
    c = client(db=True)
    db_run(
        pg_dsn,
        "INSERT INTO oauth_connections (id, org_id, provider, workspace_id, workspace_name, access_token) "
        "VALUES ($1, $2, 'notion', 'w', 'W', 'tok')",
        uuid.uuid4(), c.org_id,
    )
    sid = uuid.uuid4()
    db_run(pg_dsn, "INSERT INTO chat_sessions (id, org_id, user_id) VALUES ($1, $2, $3)", sid, c.org_id, c.user_id)
    db_run(pg_dsn, "INSERT INTO chat_messages (session_id, role, content) VALUES ($1, 'user', 'hi')", sid)
    _sign_in(c, fake_broker, _identity("google"))

    r = c.post("/account/sign-out")
    assert r.status_code == 200, r.text
    assert _user(pg_dsn, c.user_id) == {"email": LOCAL_EMAIL, "display_name": None}
    assert _identities(pg_dsn, c.user_id) == []
    assert len(db_run(pg_dsn, "SELECT 1 FROM oauth_connections WHERE org_id = $1", c.org_id)) == 1
    assert len(db_run(pg_dsn, "SELECT 1 FROM chat_sessions WHERE user_id = $1", c.user_id)) == 1
    assert len(db_run(pg_dsn, "SELECT 1 FROM chat_messages WHERE session_id = $1", sid)) == 1
    assert c.get("/account").json()["signed_in"] is False
    _drop_local_user(pg_dsn)


def _counts(dsn):
    users = db_run(dsn, "SELECT count(*) AS n FROM users")[0]["n"]
    orgs = db_run(dsn, "SELECT count(*) AS n FROM organizations")[0]["n"]
    return users, orgs


def test_auth_local_same_user_after_sign_in(client, fake_broker, pg_dsn, tmp_path):  # noqa: F811
    _drop_local_user(pg_dsn)
    c = client(db=True)
    first = c.post("/auth/local").json()
    assert json.loads((tmp_path / "desktop.json").read_text())["local_user_id"] == first["user_id"]
    counts = _counts(pg_dsn)

    _as(c, uuid.UUID(first["user_id"]), uuid.UUID(first["org_id"]))
    result = _identity("google")
    assert "Signed in as Ada Lovelace" in _sign_in(c, fake_broker, result)

    second = c.post("/auth/local").json()
    assert (second["user_id"], second["org_id"]) == (first["user_id"], first["org_id"])
    assert second["email"] == result["email"] and second["display_name"] == "Ada Lovelace"
    assert _counts(pg_dsn) == counts  # no second local user or workspace
    assert len(db_run(pg_dsn, "SELECT 1 FROM org_members WHERE user_id = $1", uuid.UUID(first["user_id"]))) == 1


def test_auth_local_backfills_local_user_id(client, pg_dsn, tmp_path):
    _drop_local_user(pg_dsn)
    c = client(db=True)
    user_id, org_id = uuid.uuid4(), uuid.uuid4()
    db_run(pg_dsn, "INSERT INTO users (id, email) VALUES ($1, $2)", user_id, LOCAL_EMAIL)
    db_run(pg_dsn, "INSERT INTO organizations (id, name, slug) VALUES ($1, 'Mine', $2)", org_id, f"m-{org_id}")
    db_run(pg_dsn, "INSERT INTO org_members (id, org_id, user_id, role) VALUES ($1, $2, $3, 'owner')", uuid.uuid4(), org_id, user_id)
    (tmp_path / "desktop.json").write_text(json.dumps({"setup_complete": True, "other": 1}))

    r = c.post("/auth/local")
    assert r.status_code == 200
    assert (r.json()["user_id"], r.json()["org_id"]) == (str(user_id), str(org_id))
    saved = json.loads((tmp_path / "desktop.json").read_text())
    assert saved["local_user_id"] == str(user_id)
    assert saved["setup_complete"] is True and saved["other"] == 1
    _drop_local_user(pg_dsn)


def test_auth_local_falls_back_to_oldest_org_owner(client, pg_dsn, tmp_path):
    """An existing install whose local user no longer has LOCAL_EMAIL and no
    key yet: the owner of the oldest workspace is the local user."""
    _drop_local_user(pg_dsn)
    c = client(db=True)
    user_id, org_id = uuid.uuid4(), uuid.uuid4()
    db_run(pg_dsn, "INSERT INTO users (id, email) VALUES ($1, $2)", user_id, f"ada-{_uid()}@example.com")
    db_run(
        pg_dsn,
        "INSERT INTO organizations (id, name, slug, created_at) VALUES ($1, 'Old', $2, '2000-01-01')",
        org_id, f"old-{org_id}",
    )
    db_run(pg_dsn, "INSERT INTO org_members (id, org_id, user_id, role) VALUES ($1, $2, $3, 'owner')", uuid.uuid4(), org_id, user_id)
    try:
        counts = _counts(pg_dsn)
        r = c.post("/auth/local").json()
        assert (r["user_id"], r["org_id"]) == (str(user_id), str(org_id))
        assert json.loads((tmp_path / "desktop.json").read_text())["local_user_id"] == str(user_id)
        assert _counts(pg_dsn) == counts
    finally:
        db_run(pg_dsn, "DELETE FROM organizations WHERE id = $1", org_id)
        db_run(pg_dsn, "DELETE FROM users WHERE id = $1", user_id)


def test_account_status_and_prompt_seen(client, fake_broker, pg_dsn, tmp_path, monkeypatch):  # noqa: F811
    c = client(db=True)
    assert c.get("/account").json() == {
        "signed_in": False,
        "provider": None,
        "email": None,
        "name": None,
        "broker_configured": True,
        "prompt_seen": False,
    }
    result = _identity("google")
    _sign_in(c, fake_broker, result)
    assert c.get("/account").json() == {
        "signed_in": True,
        "provider": "google",
        "email": result["email"],
        "name": "Ada Lovelace",
        "broker_configured": True,
        "prompt_seen": False,
    }

    (tmp_path / "desktop.json").write_text(json.dumps({"setup_complete": True}))
    r = c.post("/account/prompt-seen")
    assert r.status_code == 200
    saved = json.loads((tmp_path / "desktop.json").read_text())
    assert saved == {"setup_complete": True, "embedding_provider": "ollama", "account_prompt_seen": True}
    assert c.get("/account").json()["prompt_seen"] is True

    monkeypatch.setattr(settings, "auth_broker_url", "")
    assert c.get("/account").json()["broker_configured"] is False


def test_account_routes_owner_only(client, fake_broker):  # noqa: F811
    c = client(role="member")
    assert c.get("/account").status_code == 403
    assert c.post("/account/sign-out").status_code == 403
    assert c.post("/account/prompt-seen").status_code == 403


def test_routes_404_in_server_mode(client, fake_broker):  # noqa: F811
    c = client(mode="server")
    assert c.get("/account").status_code == 404
    assert c.post("/account/sign-out").status_code == 404
    assert c.post("/account/prompt-seen").status_code == 404

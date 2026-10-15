"""Web (server mode) Google/GitHub login: only provider-verified emails are
used to link to an existing account or to sign up."""

import json
import uuid

import httpx
import pytest

from app.api.routes import social_auth
from app.core.config import settings

from .conftest import db_run


class FakeRedis:
    def __init__(self):
        self.data: dict[str, str] = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        self.data[key] = value

    async def delete(self, key):
        self.data.pop(key, None)


@pytest.fixture
def web(make_client, external, monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(social_auth, "get_redis", lambda: redis)
    for name, value in {
        "google_oauth_client_id": "gid",
        "google_oauth_client_secret": "gsecret",
        "github_oauth_client_id": "hid",
        "github_oauth_client_secret": "hsecret",
    }.items():
        monkeypatch.setattr(settings, name, value)
    c = make_client(
        mode="server",
        db=True,
        routers=[(social_auth.google_router, "/auth/google"), (social_auth.github_router, "/auth/github")],
    )
    c.redis = redis
    c.external = external
    return c


def _state(c, provider):
    state = uuid.uuid4().hex
    c.redis.data[f"oauth_state:{provider}:{state}"] = json.dumps({"invite_token": None})
    return state


def _existing_user(dsn) -> tuple[uuid.UUID, str]:
    user_id = uuid.uuid4()
    email = f"ada-{user_id.hex[:10]}@example.com"
    org_id = uuid.uuid4()
    db_run(dsn, "INSERT INTO users (id, email) VALUES ($1, $2)", user_id, email)
    db_run(dsn, "INSERT INTO organizations (id, name, slug) VALUES ($1, 'Ada', $2)", org_id, f"ada-{org_id}")
    db_run(
        dsn, "INSERT INTO org_members (id, org_id, user_id, role) VALUES ($1, $2, $3, 'owner')",
        uuid.uuid4(), org_id, user_id,
    )
    return user_id, email


def _identity_owner(dsn, provider, provider_user_id):
    rows = db_run(
        dsn,
        "SELECT i.user_id, u.email FROM oauth_identities i JOIN users u ON u.id = i.user_id "
        "WHERE i.provider = $1 AND i.provider_user_id = $2",
        provider, provider_user_id,
    )
    return dict(rows[0]) if rows else None


def _google(userinfo):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "g-tok"})
        if request.url.path.endswith("/userinfo"):
            return httpx.Response(200, json=userinfo)
        return httpx.Response(404)

    return handler


def _github(user, emails):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "github.com":
            return httpx.Response(200, json={"access_token": "h-tok"})
        if request.url.path == "/user":
            return httpx.Response(200, json=user)
        if request.url.path == "/user/emails":
            return httpx.Response(200, json=emails)
        return httpx.Response(404)

    return handler


def _callback(c, provider):
    return c.get(f"/auth/{provider}/callback", params={"code": "c", "state": _state(c, provider)}, follow_redirects=False)


def test_google_verified_email_links_existing_user(web, pg_dsn):
    user_id, email = _existing_user(pg_dsn)
    sub = f"g-{uuid.uuid4().hex}"
    web.external.handler = _google({"sub": sub, "email": email, "email_verified": True, "name": "Ada"})
    r = _callback(web, "google")
    assert r.status_code in (302, 307), r.text
    assert _identity_owner(pg_dsn, "google", sub)["user_id"] == user_id


def test_google_unverified_email_not_used(web, pg_dsn):
    user_id, email = _existing_user(pg_dsn)
    for verified in (False, None, "true"):
        sub = f"g-{uuid.uuid4().hex}"
        info = {"sub": sub, "email": email, "name": "Mallory"}
        if verified is not None:
            info["email_verified"] = verified
        web.external.handler = _google(info)
        r = _callback(web, "google")
        # Not linked to the existing account, and no account is created from an unverified email.
        assert r.status_code == 400, r.text
        assert _identity_owner(pg_dsn, "google", sub) is None
    assert len(db_run(pg_dsn, "SELECT 1 FROM users WHERE lower(email) = lower($1)", email)) == 1


def test_github_public_email_ignored(web, pg_dsn):
    victim_id, victim_email = _existing_user(pg_dsn)
    gh_id = uuid.uuid4().int % 10**9
    verified = f"octo-{gh_id}@example.com"
    web.external.handler = _github(
        {"id": gh_id, "login": "octo", "name": "Octo", "email": victim_email},
        [
            {"email": victim_email, "primary": False, "verified": False},
            {"email": verified, "primary": True, "verified": True},
        ],
    )
    r = _callback(web, "github")
    assert r.status_code in (302, 307), r.text
    owner = _identity_owner(pg_dsn, "github", str(gh_id))
    assert owner["user_id"] != victim_id and owner["email"] == verified


def test_github_without_verified_primary_cannot_sign_up(web, pg_dsn):
    _, public_email = _existing_user(pg_dsn)
    gh_id = uuid.uuid4().int % 10**9
    web.external.handler = _github(
        {"id": gh_id, "login": "octo", "email": public_email},
        [{"email": f"x-{gh_id}@example.com", "primary": True, "verified": False}],
    )
    r = _callback(web, "github")
    assert r.status_code == 400, r.text
    assert _identity_owner(pg_dsn, "github", str(gh_id)) is None

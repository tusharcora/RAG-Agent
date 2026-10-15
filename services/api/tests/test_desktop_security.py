"""Desktop-only surface: Host allow-list (DNS rebinding) and the server-mode
404s for routes that only make sense on a single-user desktop install."""

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import auth as auth_routes
from app.api.routes import connections as connections_routes
from app.core.auth import AuthContext, require_auth
from app.core.config import settings
from app.core.db import get_session


@pytest.fixture
def desktop_client(monkeypatch, pg_dsn):
    """The real desktop entry point (app.desktop:app), no lifespan, backed by
    the pgserver test database."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app import desktop
    from app.main import app as api_app

    monkeypatch.setattr(settings, "app_mode", "desktop")
    engine = create_async_engine(pg_dsn.replace("postgresql://", "postgresql+asyncpg://", 1), poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def _session():
        async with factory() as session:
            yield session

    api_app.dependency_overrides[get_session] = _session
    try:
        yield desktop.app
    finally:
        api_app.dependency_overrides.pop(get_session, None)


def test_desktop_rejects_foreign_host(desktop_client):
    # A DNS-rebinding page reaches 127.0.0.1 but still sends its own Host.
    client = TestClient(desktop_client, base_url="http://evil.example:47821")
    r = client.post("/api/auth/local")
    assert r.status_code == 400
    assert "set-cookie" not in r.headers
    assert client.get("/api/health").status_code == 400


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost"])
def test_desktop_accepts_loopback_host(desktop_client, host):
    client = TestClient(desktop_client, base_url=f"http://{host}:47821")
    assert client.get("/api/health").status_code == 200
    r = client.post("/api/auth/local")
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "owner"
    assert "session=" in r.headers["set-cookie"]


def _server_app(monkeypatch, router, prefix):
    monkeypatch.setattr(settings, "app_mode", "server")
    app = FastAPI()
    app.include_router(router, prefix=prefix)

    async def _no_db():
        yield None  # the 404 must come before any query

    app.dependency_overrides[get_session] = _no_db
    app.dependency_overrides[require_auth] = lambda: AuthContext(org_id=uuid.uuid4(), role="owner", user_id=uuid.uuid4())
    return TestClient(app)


def test_auth_local_404_in_server_mode(monkeypatch):
    client = _server_app(monkeypatch, auth_routes.router, "/auth")
    r = client.post("/auth/local")
    assert r.status_code == 404
    assert "set-cookie" not in r.headers


def test_notion_token_404_in_server_mode(monkeypatch):
    client = _server_app(monkeypatch, connections_routes.router, "/connections")
    assert client.post("/connections/notion/token", json={"token": "secret_x"}).status_code == 404
    # 404 even before body validation, so the route's existence doesn't leak.
    assert client.post("/connections/notion/token", json={}).status_code == 404

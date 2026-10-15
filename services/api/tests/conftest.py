import asyncio
import json
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import setup as setup_routes
from app.core.auth import AuthContext, require_auth
from app.core.db import get_session
from app.core.config import settings
from app.integrations import ollama


REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="session")
def pg_dsn():
    """A real embedded Postgres (pgserver) with the repo's schema applied."""
    import pgserver

    sys.path.insert(0, str(REPO / "desktop"))
    from launcher import migrate

    tmp = tempfile.mkdtemp(prefix="ka-test-pg-", dir="/tmp")
    server = pgserver.get_server(Path(tmp) / "pgdata", cleanup_mode="stop")
    uri = server.get_uri()
    asyncio.run(migrate(uri, REPO / "infra" / "postgres"))
    try:
        yield uri
    finally:
        server.cleanup()
        shutil.rmtree(tmp, ignore_errors=True)


def db_run(dsn, sql, *args):
    """Run one statement against the test database; returns fetched rows."""
    import asyncpg

    async def go():
        conn = await asyncpg.connect(dsn)
        try:
            return await conn.fetch(sql, *args)
        finally:
            await conn.close()

    return asyncio.run(go())


@pytest.fixture
def make_client(tmp_path, monkeypatch, request):
    """Build a test app with just the setup router (no lifespan). Pass
    db=True to back it with the pgserver database and a real org + user."""

    def _make(handler=None, mode="desktop", role="owner", db=False, routers=None, base_url="http://testserver"):
        monkeypatch.setattr(settings, "app_mode", mode)
        monkeypatch.setattr(settings, "desktop_data_dir", str(tmp_path))
        if handler is not None:
            monkeypatch.setattr(ollama, "TRANSPORT", httpx.MockTransport(handler))
        setup_routes._IN_FLIGHT.clear()
        app = FastAPI()
        for router, prefix in routers or [(setup_routes.router, "/setup")]:
            app.include_router(router, prefix=prefix)
        org_id, user_id = uuid.uuid4(), uuid.uuid4()
        if db:
            from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
            from sqlalchemy.pool import NullPool

            from cryptography.fernet import Fernet

            monkeypatch.setattr(settings, "token_encryption_key", Fernet.generate_key().decode())
            dsn = request.getfixturevalue("pg_dsn")
            db_run(dsn, "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T', $2)", org_id, f"t-{org_id}")
            db_run(dsn, "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, 'x')", user_id, f"{user_id}@t.io")
            # NullPool: TestClient runs each request on its own event loop.
            engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://", 1), poolclass=NullPool)
            factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

            async def _session():
                async with factory() as session:
                    yield session

            app.dependency_overrides[get_session] = _session
        app.dependency_overrides[require_auth] = lambda: AuthContext(org_id=org_id, role=role, user_id=user_id)
        client = TestClient(app, base_url=base_url)
        client.org_id = org_id
        client.user_id = user_id
        return client

    yield _make
    setup_routes._IN_FLIGHT.clear()


def ndjson(*objs):
    return "\n".join(json.dumps(o) for o in objs) + "\n"


@pytest.fixture
def restarts(monkeypatch):
    calls = []
    monkeypatch.setattr(setup_routes.desktop_state, "schedule_restart", lambda *a, **k: calls.append(1))
    return calls


@pytest.fixture
def external(monkeypatch):
    """Route non-Ollama outbound httpx calls (Voyage, Anthropic, OpenAI) to a
    handler set by the test: external.handler = lambda request: Response."""

    class Holder:
        handler = None

    holder = Holder()
    real = httpx.AsyncClient

    class Patched(real):
        def __init__(self, *a, **kw):
            if kw.get("transport") is None:
                kw["transport"] = httpx.MockTransport(lambda req: holder.handler(req))
            super().__init__(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", Patched)
    return holder

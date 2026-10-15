import json
import uuid

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import setup as setup_routes
from app.core.auth import AuthContext, require_auth
from app.core.config import settings
from app.integrations import ollama


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    """Build a test app with just the setup router (no lifespan, no database)."""

    def _make(handler, mode="desktop", role="owner"):
        monkeypatch.setattr(settings, "app_mode", mode)
        monkeypatch.setattr(settings, "desktop_data_dir", str(tmp_path))
        monkeypatch.setattr(ollama, "TRANSPORT", httpx.MockTransport(handler))
        setup_routes._IN_FLIGHT.clear()
        app = FastAPI()
        app.include_router(setup_routes.router, prefix="/setup")
        app.dependency_overrides[require_auth] = lambda: AuthContext(
            org_id=uuid.uuid4(), role=role, user_id=uuid.uuid4()
        )
        return TestClient(app)

    yield _make
    setup_routes._IN_FLIGHT.clear()


def ndjson(*objs):
    return "\n".join(json.dumps(o) for o in objs) + "\n"

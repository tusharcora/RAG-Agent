"""ASGI entry point for desktop mode: the UI and the API on one local origin.

    uvicorn app.desktop:app --host 127.0.0.1 --port <port>

The regular API (app.main:app) is mounted under /api, matching the path the
frontend already calls (it proxies /api in server mode), and the static
export of the frontend (DESKTOP_STATIC_DIR) is served from /.
"""

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.main import app as api_app

app = FastAPI(title="Knowledge Agent (desktop)", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/api", api_app)

# Mounting a sub-app doesn't run its lifespan; forward it.
app.router.lifespan_context = api_app.router.lifespan_context

static_dir = os.environ.get("DESKTOP_STATIC_DIR")
if static_dir and Path(static_dir).is_dir():
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="ui")

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.telemetry import instrument_app, setup_telemetry
from app.core.queue import get_queue_connection, close_queue_connection
from app.core.db import init_engine, dispose_engine
from app.api.routes import health, events, notion_oauth, jira_oauth, sync, query, sessions, connections, documents, auth, org


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_telemetry()
    await init_engine()
    await get_queue_connection()
    yield
    await close_queue_connection()
    await dispose_engine()


app = FastAPI(
    title="Shared Backbone API",
    description="Event ingestion + query API shared by the RAG agent and code review bot",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

instrument_app(app)

app.include_router(health.router, tags=["health"])
app.include_router(events.router, prefix="/events", tags=["events"])
app.include_router(auth.router, prefix="/auth", tags=["auth"])

app.include_router(notion_oauth.router, prefix="/oauth/notion", tags=["rag-agent"])
app.include_router(jira_oauth.router, prefix="/oauth/jira", tags=["rag-agent"])
app.include_router(sync.router, prefix="/sync", tags=["rag-agent"])
app.include_router(query.router, prefix="/query", tags=["rag-agent"])
app.include_router(sessions.router, prefix="/sessions", tags=["rag-agent"])
app.include_router(connections.router, prefix="/connections", tags=["rag-agent"])
app.include_router(documents.router, prefix="/documents", tags=["rag-agent"])
app.include_router(org.router, prefix="/org", tags=["auth"])

# Code review bot routers get mounted here once that project starts, e.g.:
# from app.api.routes import github_webhook
# app.include_router(github_webhook.router, prefix="/webhooks/github", tags=["code-review-bot"])

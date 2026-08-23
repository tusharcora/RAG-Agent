from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_api_key
from app.core.db import get_session
from app.core.queue import publish_event
from app.models.event_log import EventLog

router = APIRouter()


class EventIn(BaseModel):
    routing_key: str  # e.g. "rag.notion_page_updated" or "codereview.pull_request_opened"
    dedupe_key: str
    payload: dict[str, Any]


@router.post("/publish")
async def publish(event: EventIn, session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    """Generic entrypoint used to smoke-test the queue end-to-end.

    Project-specific ingress (GitHub webhook receiver with signature
    verification, Notion/Jira sync trigger) will call `publish_event`
    directly with a real routing key rather than going through this
    generic route — this is here mainly to prove the backbone works
    before either project's domain logic exists.
    """
    event_id = await publish_event(session, event.routing_key, event.payload, event.dedupe_key)
    return {"status": "queued", "event_log_id": str(event_id)}


class EventLogOut(BaseModel):
    id: str
    routing_key: str
    dedupe_key: str
    payload: dict[str, Any]
    status: str
    error: str | None
    trace_id: str | None
    created_at: datetime
    updated_at: datetime


@router.get("/recent", dependencies=[Depends(require_api_key)])
async def recent_events(
    limit: int = Query(50, le=200),
    status: str | None = None,
    routing_key: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[EventLogOut]:
    stmt = select(EventLog).order_by(EventLog.created_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(EventLog.status == status)
    if routing_key:
        stmt = stmt.where(EventLog.routing_key == routing_key)

    result = await session.execute(stmt)
    rows = result.scalars().all()
    return [
        EventLogOut(
            id=str(row.id),
            routing_key=row.routing_key,
            dedupe_key=row.dedupe_key,
            payload=row.payload,
            status=row.status,
            error=row.error,
            trace_id=row.trace_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        for row in rows
    ]

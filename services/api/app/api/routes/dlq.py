import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthContext, require_auth, require_role
from app.core.db import get_session
from app.core.queue import publish_event
from app.models.event_log import EventLog

router = APIRouter(dependencies=[Depends(require_auth)])


class DlqEventOut(BaseModel):
    id: str
    routing_key: str
    error: str | None
    created_at: datetime


@router.get("")
async def list_dlq_events(
    auth: AuthContext = Depends(require_auth), session: AsyncSession = Depends(get_session)
) -> list[DlqEventOut]:
    require_role(auth, "owner", "admin")
    result = await session.execute(
        select(EventLog)
        .where(EventLog.status == "dead_lettered", EventLog.org_id == auth.org_id)
        .order_by(EventLog.created_at.desc())
        .limit(50)
    )
    rows = result.scalars().all()
    return [DlqEventOut(id=str(row.id), routing_key=row.routing_key, error=row.error, created_at=row.created_at) for row in rows]


class RedriveOut(BaseModel):
    event_log_id: str


@router.post("/{event_log_id}/redrive")
async def redrive(
    event_log_id: uuid.UUID,
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> RedriveOut:
    require_role(auth, "owner", "admin")

    result = await session.execute(
        select(EventLog).where(
            EventLog.id == event_log_id, EventLog.status == "dead_lettered", EventLog.org_id == auth.org_id
        )
    )
    event = result.scalar_one_or_none()
    if event is None:
        raise HTTPException(status_code=404, detail="Dead-lettered event not found")

    # Same dedupe_key as the original — safe to reuse because handle_message's
    # failure path (services/worker/app/main.py) always calls clear_dedupe()
    # before dead-lettering, so the Redis claim from the original attempt is
    # already released. Publishing under the original dedupe_key rather than
    # a synthetic one keeps a redrive indistinguishable from a fresh delivery
    # of the same event, which is what dedupe is meant to model.
    #
    # This creates a brand-new event_log row (status="received") rather than
    # resurrecting the dead-lettered one — the old row stays untouched as the
    # historical record of the original failure.
    new_id = await publish_event(session, event.routing_key, event.payload, event.dedupe_key, org_id=event.org_id)
    return RedriveOut(event_log_id=str(new_id))

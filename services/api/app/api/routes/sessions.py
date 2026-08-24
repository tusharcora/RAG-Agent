import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthContext, require_auth
from app.core.db import get_session
from app.core.session_store import get_session_detail, list_sessions, set_feedback

router = APIRouter(dependencies=[Depends(require_auth)])


class SessionSummary(BaseModel):
    session_id: str
    preview: str | None
    last_active: float
    turn_count: int


class HistoryTurn(BaseModel):
    id: str
    role: str
    content: str
    feedback: str | None = None


class SessionDetail(BaseModel):
    session_id: str
    history: list[HistoryTurn]


@router.get("")
async def sessions(
    limit: int = Query(20, le=100),
    search: str | None = Query(None),
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> list[SessionSummary]:
    return await list_sessions(session, auth.org_id, auth.user_id, limit, search=search)


@router.get("/{session_id}")
async def session_detail(
    session_id: str,
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> SessionDetail:
    # A malformed id (not even a valid UUID) can't belong to anyone —
    # indistinguishable from "not found" rather than a 500, same as an id
    # that's well-formed but doesn't resolve for this user.
    try:
        history = await get_session_detail(session, auth.org_id, auth.user_id, session_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Session not found")
    if history is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return SessionDetail(session_id=session_id, history=history)


class FeedbackRequest(BaseModel):
    feedback: Literal["up", "down"] | None = None  # None clears any existing feedback


@router.post("/{session_id}/messages/{message_id}/feedback", status_code=204)
async def submit_feedback(
    session_id: str,
    message_id: str,
    request: FeedbackRequest,
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> None:
    # session_id isn't used for scoping here — set_feedback verifies
    # ownership by joining message_id through to its own chat_sessions row,
    # which is a strictly tighter check than trusting the session_id in the
    # URL to match. A malformed message_id can't belong to anyone either,
    # same 404 as a well-formed one that doesn't resolve for this user.
    try:
        mid = uuid.UUID(message_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Message not found")
    updated = await set_feedback(session, auth.org_id, auth.user_id, mid, request.feedback)
    if not updated:
        raise HTTPException(status_code=404, detail="Message not found")

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthContext, require_auth
from app.core.db import get_session
from app.core.session_store import get_session_detail, list_sessions

router = APIRouter(dependencies=[Depends(require_auth)])


class SessionSummary(BaseModel):
    session_id: str
    preview: str | None
    last_active: float
    turn_count: int


class HistoryTurn(BaseModel):
    role: str
    content: str


class SessionDetail(BaseModel):
    session_id: str
    history: list[HistoryTurn]


@router.get("")
async def sessions(
    limit: int = Query(20, le=100),
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> list[SessionSummary]:
    return await list_sessions(session, auth.org_id, auth.user_id, limit)


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

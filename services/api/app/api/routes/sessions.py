from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.core.auth import AuthContext, require_auth
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
async def sessions(limit: int = Query(20, le=100), auth: AuthContext = Depends(require_auth)) -> list[SessionSummary]:
    return await list_sessions(auth.org_id, auth.user_id, limit)


@router.get("/{session_id}")
async def session_detail(session_id: str, auth: AuthContext = Depends(require_auth)) -> SessionDetail:
    # Scoped Redis keys mean a session_id belonging to another user simply
    # doesn't resolve here — no separate ownership check needed.
    history = await get_session_detail(auth.org_id, auth.user_id, session_id)
    if history is None:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    return SessionDetail(session_id=session_id, history=history)

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.core.auth import require_api_key
from app.core.session_store import get_session_detail, list_sessions

router = APIRouter(dependencies=[Depends(require_api_key)])


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
async def sessions(limit: int = Query(20, le=100)) -> list[SessionSummary]:
    return await list_sessions(limit)


@router.get("/{session_id}")
async def session_detail(session_id: str) -> SessionDetail:
    history = await get_session_detail(session_id)
    if history is None:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    return SessionDetail(session_id=session_id, history=history)

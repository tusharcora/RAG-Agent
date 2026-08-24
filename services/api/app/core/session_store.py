import uuid

from sqlalchemy import desc, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.rag import ChatMessage, ChatSession


async def _owned_by(session: AsyncSession, session_id: uuid.UUID, org_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    result = await session.execute(
        select(ChatSession.id).where(
            ChatSession.id == session_id, ChatSession.org_id == org_id, ChatSession.user_id == user_id
        )
    )
    return result.scalar_one_or_none() is not None


async def load_history(session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, session_id: str) -> list[dict]:
    """Returns [] both for a brand-new session_id that hasn't been written to
    yet (a new conversation) and for one belonging to a different user — the
    org_id+user_id scoping in the WHERE clause means a foreign session_id
    simply doesn't resolve here, no separate ownership check needed.

    Used only to build Gemini's conversation context for the *next* query, so
    this caps to the most recent session_history_max_turns turns (unlike
    get_session_detail below, which returns full history for replay) — the
    underlying storage is durable and complete regardless of this cap, this
    just bounds how much of it gets sent to the model on every subsequent
    question."""
    sid = uuid.UUID(session_id)
    if not await _owned_by(session, sid, org_id, user_id):
        return []
    rows = await session.execute(
        select(ChatMessage.id, ChatMessage.role, ChatMessage.content, ChatMessage.feedback)
        .where(ChatMessage.session_id == sid)
        .order_by(desc(ChatMessage.created_at))
        .limit(settings.session_history_max_turns * 2)
    )
    return [
        {"id": str(r.id), "role": r.role, "content": r.content, "feedback": r.feedback}
        for r in reversed(rows.all())
    ]


async def append_history(
    session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, session_id: str, role: str, content: str
) -> uuid.UUID:
    """Upserts the chat_sessions row (creating it on the first call for a new
    session_id) and inserts the message, in one commit. Called at the very
    start of a turn for the user's question and again once the assistant's
    answer is known — see query.py — so a brand-new conversation is durably
    recorded, and visible via GET /sessions, the instant a question is sent,
    not once the full answer finishes streaming.

    Returns the new message's id — query.py needs the assistant message's id
    to hand back to the frontend so it can submit thumbs up/down feedback
    against it."""
    sid = uuid.UUID(session_id)
    insert_stmt = pg_insert(ChatSession).values(
        id=sid,
        org_id=org_id,
        user_id=user_id,
        preview=content[:140] if role == "user" else None,
        turn_count=1,
    )
    upsert_stmt = insert_stmt.on_conflict_do_update(
        index_elements=[ChatSession.id],
        set_={
            "updated_at": func.now(),
            "turn_count": ChatSession.turn_count + 1,
            # COALESCE keeps whatever's already stored — this only ever takes
            # effect on the *first* user message for a session (excluded.preview
            # is NULL on every other call, including every assistant call).
            "preview": func.coalesce(ChatSession.preview, insert_stmt.excluded.preview),
        },
    )
    await session.execute(upsert_stmt)
    message_id = uuid.uuid4()
    session.add(ChatMessage(id=message_id, session_id=sid, role=role, content=content))
    await session.commit()
    return message_id


async def set_feedback(
    session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, message_id: uuid.UUID, feedback: str | None
) -> bool:
    """Updates chat_messages.feedback, scoped through a join to chat_sessions
    the same way _owned_by scopes reads above — a message_id that doesn't
    exist, or belongs to a different org/user, updates zero rows. Returns
    whether a row was actually updated so the route can 404 rather than
    silently succeed on a foreign or nonexistent message id."""
    result = await session.execute(
        update(ChatMessage)
        .where(
            ChatMessage.id == message_id,
            ChatMessage.session_id == ChatSession.id,
            ChatSession.org_id == org_id,
            ChatSession.user_id == user_id,
        )
        .values(feedback=feedback)
    )
    await session.commit()
    return result.rowcount > 0


async def list_sessions(session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, limit: int = 20) -> list[dict]:
    """Newest-active first, scoped to one user within one org."""
    result = await session.execute(
        select(ChatSession.id, ChatSession.preview, ChatSession.updated_at, ChatSession.turn_count)
        .where(ChatSession.org_id == org_id, ChatSession.user_id == user_id)
        .order_by(ChatSession.updated_at.desc())
        .limit(limit)
    )
    return [
        {
            "session_id": str(r.id),
            "preview": r.preview,
            "last_active": r.updated_at.timestamp(),
            "turn_count": r.turn_count,
        }
        for r in result
    ]


async def get_session_detail(
    session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, session_id: str
) -> list[dict] | None:
    """None (→ 404 at the route) when the session doesn't exist or belongs to
    someone else; [] is a legitimate (if unusual) result for a session that
    exists but genuinely has no messages, so it can't double as "not found"."""
    sid = uuid.UUID(session_id)
    if not await _owned_by(session, sid, org_id, user_id):
        return None
    rows = await session.execute(
        select(ChatMessage.id, ChatMessage.role, ChatMessage.content, ChatMessage.feedback)
        .where(ChatMessage.session_id == sid)
        .order_by(ChatMessage.created_at)
    )
    return [{"id": str(r.id), "role": r.role, "content": r.content, "feedback": r.feedback} for r in rows]

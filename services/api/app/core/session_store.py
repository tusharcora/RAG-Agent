import uuid

from sqlalchemy import desc, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert, websearch_to_tsquery
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
        select(ChatMessage.role, ChatMessage.content)
        .where(ChatMessage.session_id == sid)
        .order_by(desc(ChatMessage.created_at))
        .limit(settings.session_history_max_turns * 2)
    )
    return [{"role": r.role, "content": r.content} for r in reversed(rows.all())]


async def append_history(
    session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, session_id: str, role: str, content: str
) -> None:
    """Upserts the chat_sessions row (creating it on the first call for a new
    session_id) and inserts the message, in one commit. Called at the very
    start of a turn for the user's question and again once the assistant's
    answer is known — see query.py — so a brand-new conversation is durably
    recorded, and visible via GET /sessions, the instant a question is sent,
    not once the full answer finishes streaming."""
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
    session.add(ChatMessage(id=uuid.uuid4(), session_id=sid, role=role, content=content))
    await session.commit()


async def list_sessions(
    session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, limit: int = 20, search: str | None = None
) -> list[dict]:
    """Newest-active first, scoped to one user within one org. With `search`,
    joins to chat_messages and ranks by each session's single best-matching
    message instead (func.max(ts_rank(...)) grouped by session) — a
    conversation can have several matching messages, and without the GROUP BY
    it'd surface once per match instead of once per session. Same
    websearch_to_tsquery/ts_rank pattern as documents.py's list_documents,
    against search_vector (infra/postgres/009_chat_search.sql, GENERATED from
    chat_messages.content, not chat_sessions.preview — the preview is only
    the opening ~140 chars of the first message)."""
    if search:
        tsquery = websearch_to_tsquery("english", search)
        result = await session.execute(
            select(ChatSession.id, ChatSession.preview, ChatSession.updated_at, ChatSession.turn_count)
            .join(ChatMessage, ChatMessage.session_id == ChatSession.id)
            .where(
                ChatSession.org_id == org_id,
                ChatSession.user_id == user_id,
                ChatMessage.search_vector.op("@@")(tsquery),
            )
            .group_by(ChatSession.id)
            .order_by(func.max(func.ts_rank(ChatMessage.search_vector, tsquery)).desc())
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
        select(ChatMessage.role, ChatMessage.content).where(ChatMessage.session_id == sid).order_by(ChatMessage.created_at)
    )
    return [{"role": r.role, "content": r.content} for r in rows]

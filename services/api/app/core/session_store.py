import json
import time
import uuid

from app.core.config import settings
from app.core.redis import get_redis


def _index_key(org_id: uuid.UUID, user_id: uuid.UUID) -> str:
    # Per-user, not global — a global sorted set would let any authenticated
    # caller list every user's sessions via GET /sessions.
    return f"rag:sessions:index:{org_id}:{user_id}"


def _history_key(org_id: uuid.UUID, user_id: uuid.UUID, session_id: str) -> str:
    return f"rag:session:{org_id}:{user_id}:{session_id}:history"


def _preview_key(org_id: uuid.UUID, user_id: uuid.UUID, session_id: str) -> str:
    return f"rag:session:{org_id}:{user_id}:{session_id}:preview"


async def load_history(org_id: uuid.UUID, user_id: uuid.UUID, session_id: str) -> list[dict]:
    r = get_redis()
    raw = await r.lrange(_history_key(org_id, user_id, session_id), 0, -1)
    return [json.loads(item) for item in raw]


async def append_history(org_id: uuid.UUID, user_id: uuid.UUID, session_id: str, role: str, content: str) -> None:
    r = get_redis()
    key = _history_key(org_id, user_id, session_id)
    await r.rpush(key, json.dumps({"role": role, "content": content}))
    await r.ltrim(key, -settings.session_history_max_turns * 2, -1)
    await r.expire(key, settings.session_history_ttl_seconds)

    await r.zadd(_index_key(org_id, user_id), {session_id: time.time()})

    preview_key = _preview_key(org_id, user_id, session_id)
    if role == "user" and not await r.exists(preview_key):
        await r.set(preview_key, content[:140], ex=settings.session_history_ttl_seconds)
    else:
        await r.expire(preview_key, settings.session_history_ttl_seconds)


async def list_sessions(org_id: uuid.UUID, user_id: uuid.UUID, limit: int = 20) -> list[dict]:
    """Newest-active first, scoped to one user within one org. Lazily prunes
    index entries whose history key has already expired instead of running a
    separate cleanup job — sessions are short-term memory by design, a stale
    index entry is just UI noise until the next read notices and removes it."""
    r = get_redis()
    index_key = _index_key(org_id, user_id)
    # over-fetch since some candidates may have already expired
    candidates = await r.zrevrange(index_key, 0, max(limit * 3 - 1, 19), withscores=True)

    out: list[dict] = []
    for session_id, score in candidates:
        if len(out) >= limit:
            break
        history_key = _history_key(org_id, user_id, session_id)
        if not await r.exists(history_key):
            await r.zrem(index_key, session_id)
            continue
        turn_count = await r.llen(history_key)
        preview = await r.get(_preview_key(org_id, user_id, session_id))
        out.append({"session_id": session_id, "preview": preview, "last_active": score, "turn_count": turn_count})
    return out


async def get_session_detail(org_id: uuid.UUID, user_id: uuid.UUID, session_id: str) -> list[dict] | None:
    r = get_redis()
    if not await r.exists(_history_key(org_id, user_id, session_id)):
        return None
    return await load_history(org_id, user_id, session_id)

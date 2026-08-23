import redis.asyncio as redis

from app.core.config import settings

_redis: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis


async def already_processed(dedupe_key: str) -> bool:
    """SETNX-style check: returns True if this key was already seen recently.

    This is what makes GitHub webhook retries or a re-triggered Notion sync
    safe to process twice at the transport layer — the worker only does the
    real work once per dedupe_key within the TTL window.

    The SETNX claims the key up front, before processing succeeds — on a
    failure the caller MUST call clear_dedupe() so a subsequent retry isn't
    incorrectly skipped as a "duplicate" of a delivery that never actually
    completed (which would otherwise silently mask the failure).
    """
    r = get_redis()
    was_set = await r.set(
        name=f"dedupe:{dedupe_key}",
        value="1",
        nx=True,
        ex=settings.dedupe_ttl_seconds,
    )
    # SET ... NX returns True/None depending on client; normalize
    return not bool(was_set)


async def clear_dedupe(dedupe_key: str) -> None:
    """Releases a dedupe key claimed by already_processed() when processing
    did NOT actually succeed, so a requeued retry (or a manual DLQ replay)
    gets a genuine second attempt instead of being skipped as a duplicate."""
    r = get_redis()
    await r.delete(f"dedupe:{dedupe_key}")

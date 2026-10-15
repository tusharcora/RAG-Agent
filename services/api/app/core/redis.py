import redis.asyncio as redis

from app.core.config import settings

_redis: redis.Redis | None = None


def get_redis() -> redis.Redis:
    """REDIS_URL=memory:// (desktop mode) swaps in an in-process fake with the
    same async API. Each process gets its own, which is fine for what Redis
    holds here: rate-limit windows, OAuth state, dedupe keys, usage counters."""
    global _redis
    if _redis is None:
        if settings.redis_url.startswith("memory://"):
            from fakeredis import FakeAsyncRedis

            _redis = FakeAsyncRedis(decode_responses=True)
        else:
            _redis = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis

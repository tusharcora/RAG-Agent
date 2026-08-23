import time

from fastapi import HTTPException, Request

from app.core.redis import get_redis


async def check_rate_limit(key: str, per_minute: int) -> None:
    """Core fixed-window Redis counter, keyed by whatever the caller passes
    (an IP, a user id, an email) — shared by rate_limiter() below and any
    identity-keyed check (e.g. /auth/login, keyed by submitted email) that
    doesn't fit the per-request-IP shape."""
    bucket = int(time.time() // 60)
    r = get_redis()
    count = await r.incr(f"ratelimit:{key}:{bucket}")
    if count == 1:
        await r.expire(f"ratelimit:{key}:{bucket}", 60)
    if count > per_minute:
        raise HTTPException(status_code=429, detail="Rate limit exceeded, try again shortly")


def rate_limiter(route: str, per_minute: int):
    """FastAPI dependency factory: fixed-window per-IP rate limit backed by Redis.

    Cost/abuse guard for endpoints that spend real Anthropic/Voyage money per
    call (e.g. /query) or that fan out many external API calls (e.g. /sync/*)
    — not a security boundary, just a cap on accidental or malicious cost blowouts.
    """

    async def _check(request: Request) -> None:
        client_ip = request.client.host if request.client else "unknown"
        await check_rate_limit(f"{route}:{client_ip}", per_minute)

    return _check

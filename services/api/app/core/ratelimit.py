import time

from fastapi import HTTPException, Request

from app.core.redis import get_redis


def rate_limiter(route: str, per_minute: int):
    """FastAPI dependency factory: fixed-window per-IP rate limit backed by Redis.

    Cost/abuse guard for endpoints that spend real Anthropic/Voyage money per
    call (e.g. /query) or that fan out many external API calls (e.g. /sync/*)
    — not a security boundary, just a cap on accidental or malicious cost blowouts.
    """

    async def _check(request: Request) -> None:
        client_ip = request.client.host if request.client else "unknown"
        bucket = int(time.time() // 60)
        key = f"ratelimit:{route}:{client_ip}:{bucket}"

        r = get_redis()
        count = await r.incr(key)
        if count == 1:
            await r.expire(key, 60)
        if count > per_minute:
            raise HTTPException(status_code=429, detail="Rate limit exceeded, try again shortly")

    return _check

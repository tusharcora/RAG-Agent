from fastapi import Header, HTTPException

from app.core.config import settings


async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """FastAPI dependency: enforces X-API-Key when API_SHARED_SECRET is set.

    Off by default (empty secret = local dev). Not applied to /oauth/* callbacks,
    which are hit by the provider's redirect rather than a direct caller.
    """
    if not settings.api_shared_secret:
        return
    if x_api_key != settings.api_shared_secret:
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key")

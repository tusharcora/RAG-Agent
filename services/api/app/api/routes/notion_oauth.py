import logging
import secrets
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.core.redis import get_redis
from app.models.rag import OAuthConnection

logger = logging.getLogger(__name__)

router = APIRouter()

NOTION_AUTHORIZE_URL = "https://api.notion.com/v1/oauth/authorize"
NOTION_TOKEN_URL = "https://api.notion.com/v1/oauth/token"


@router.get("/authorize")
async def authorize() -> RedirectResponse:
    state = secrets.token_urlsafe(32)
    await get_redis().set(f"oauth_state:notion:{state}", "1", ex=settings.oauth_state_ttl_seconds)

    params = {
        "client_id": settings.notion_client_id,
        "redirect_uri": settings.notion_redirect_uri,
        "response_type": "code",
        "owner": "user",
        "state": state,
    }
    return RedirectResponse(str(httpx.URL(NOTION_AUTHORIZE_URL, params=params)))


@router.get("/callback")
async def callback(
    code: str = Query(...),
    state: str = Query(...),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    redis = get_redis()
    state_key = f"oauth_state:notion:{state}"
    if not await redis.get(state_key):
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
    await redis.delete(state_key)

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            NOTION_TOKEN_URL,
            auth=(settings.notion_client_id, settings.notion_client_secret),
            json={"grant_type": "authorization_code", "code": code, "redirect_uri": settings.notion_redirect_uri},
        )
        resp.raise_for_status()
        data = resp.json()

    result = await session.execute(select(OAuthConnection).where(OAuthConnection.provider == "notion"))
    connection = result.scalar_one_or_none()
    if connection is None:
        # UNIQUE(provider): single-tenant — reconnecting overwrites the prior connection.
        connection = OAuthConnection(id=uuid.uuid4(), provider="notion", access_token="")
        session.add(connection)

    connection.workspace_id = data.get("workspace_id", "")
    connection.workspace_name = data.get("workspace_name") or "Notion workspace"
    connection.access_token = data["access_token"]
    connection.bot_id = data.get("bot_id")
    connection.refresh_token = None  # Notion tokens don't expire
    connection.expires_at = None
    connection.updated_at = datetime.now(timezone.utc)
    await session.commit()

    logger.info("Notion connected: workspace=%s", connection.workspace_name)
    return RedirectResponse(f"{settings.frontend_url}/connections?connected=notion")

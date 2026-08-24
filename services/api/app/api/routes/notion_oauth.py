import logging
import secrets
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthContext, require_auth
from app.core.config import settings
from app.core.crypto import encrypt_token
from app.core.db import get_session
from app.core.redis import get_redis
from app.models.rag import OAuthConnection

logger = logging.getLogger(__name__)

router = APIRouter()

NOTION_AUTHORIZE_URL = "https://api.notion.com/v1/oauth/authorize"
NOTION_TOKEN_URL = "https://api.notion.com/v1/oauth/token"


@router.get("/authorize")
async def authorize(auth: AuthContext = Depends(require_auth)) -> RedirectResponse:
    # Without this check, an unset NOTION_CLIENT_ID silently produces a
    # client_id=<empty> redirect to Notion's own authorize endpoint — the
    # user lands on a confusing raw Notion API error instead of a clear
    # message from us. Same guard pattern as social_auth.py's google/github
    # /authorize.
    if not settings.notion_client_id:
        raise HTTPException(status_code=503, detail="Notion is not configured")

    # /authorize is hit directly by the frontend (unlike /callback, which is
    # hit by Notion's redirect and can't carry an app auth header), so it can
    # sit behind require_auth. Storing org_id as the CSRF state's Redis value
    # is the whole org-attribution mechanism — no other side-channel needed:
    # if this key expires before /callback runs, org attribution and CSRF
    # validity are lost together and /callback cleanly 400s, same as a CSRF
    # failure does today.
    state = secrets.token_urlsafe(32)
    await get_redis().set(f"oauth_state:notion:{state}", str(auth.org_id), ex=settings.oauth_state_ttl_seconds)

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
    org_id_raw = await redis.get(state_key)
    if not org_id_raw:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
    await redis.delete(state_key)
    org_id = uuid.UUID(org_id_raw)

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            NOTION_TOKEN_URL,
            auth=(settings.notion_client_id, settings.notion_client_secret),
            json={"grant_type": "authorization_code", "code": code, "redirect_uri": settings.notion_redirect_uri},
        )
        resp.raise_for_status()
        data = resp.json()

    result = await session.execute(
        select(OAuthConnection).where(OAuthConnection.provider == "notion", OAuthConnection.org_id == org_id)
    )
    connection = result.scalar_one_or_none()
    if connection is None:
        # UNIQUE(org_id, provider): reconnecting overwrites this org's prior connection.
        connection = OAuthConnection(id=uuid.uuid4(), org_id=org_id, provider="notion", access_token="")
        session.add(connection)

    connection.workspace_id = data.get("workspace_id", "")
    connection.workspace_name = data.get("workspace_name") or "Notion workspace"
    connection.access_token = encrypt_token(data["access_token"])
    connection.bot_id = data.get("bot_id")
    connection.refresh_token = None  # Notion tokens don't expire
    connection.expires_at = None
    connection.updated_at = datetime.now(timezone.utc)
    await session.commit()

    logger.info("Notion connected: workspace=%s", connection.workspace_name)
    return RedirectResponse(f"{settings.frontend_url}/connections?connected=notion")

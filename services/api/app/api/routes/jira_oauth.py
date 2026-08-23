import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

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

JIRA_AUTHORIZE_URL = "https://auth.atlassian.com/authorize"
JIRA_TOKEN_URL = "https://auth.atlassian.com/oauth/token"
JIRA_ACCESSIBLE_RESOURCES_URL = "https://api.atlassian.com/oauth/token/accessible-resources"
JIRA_SCOPES = "read:jira-work read:jira-user offline_access"


@router.get("/authorize")
async def authorize() -> RedirectResponse:
    state = secrets.token_urlsafe(32)
    await get_redis().set(f"oauth_state:jira:{state}", "1", ex=settings.oauth_state_ttl_seconds)

    params = {
        "audience": "api.atlassian.com",
        "client_id": settings.jira_client_id,
        "scope": JIRA_SCOPES,
        "redirect_uri": settings.jira_redirect_uri,
        "state": state,
        "response_type": "code",
        "prompt": "consent",
    }
    return RedirectResponse(str(httpx.URL(JIRA_AUTHORIZE_URL, params=params)))


@router.get("/callback")
async def callback(
    code: str = Query(...),
    state: str = Query(...),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    redis = get_redis()
    state_key = f"oauth_state:jira:{state}"
    if not await redis.get(state_key):
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
    await redis.delete(state_key)

    async with httpx.AsyncClient(timeout=30) as client:
        token_resp = await client.post(
            JIRA_TOKEN_URL,
            json={
                "grant_type": "authorization_code",
                "client_id": settings.jira_client_id,
                "client_secret": settings.jira_client_secret,
                "code": code,
                "redirect_uri": settings.jira_redirect_uri,
            },
        )
        token_resp.raise_for_status()
        token_data = token_resp.json()

        resources_resp = await client.get(
            JIRA_ACCESSIBLE_RESOURCES_URL,
            headers={"Authorization": f"Bearer {token_data['access_token']}"},
        )
        resources_resp.raise_for_status()
        resources = resources_resp.json()

    if not resources:
        raise HTTPException(status_code=400, detail="No accessible Jira sites for this account")
    site = resources[0]  # single-tenant: first accessible site

    now = datetime.now(timezone.utc)
    result = await session.execute(select(OAuthConnection).where(OAuthConnection.provider == "jira"))
    connection = result.scalar_one_or_none()
    if connection is None:
        connection = OAuthConnection(id=uuid.uuid4(), provider="jira", access_token="")
        session.add(connection)

    connection.workspace_id = site["id"]  # Jira Cloud id, used in api.atlassian.com/ex/jira/{id}/... calls
    connection.workspace_name = site.get("name") or site["url"]
    connection.site_url = site["url"]
    connection.access_token = token_data["access_token"]
    connection.refresh_token = token_data.get("refresh_token")
    connection.expires_at = now + timedelta(seconds=token_data.get("expires_in", 3600))
    connection.bot_id = None
    connection.updated_at = now
    await session.commit()

    logger.info("Jira connected: site=%s", connection.workspace_name)
    return RedirectResponse(f"{settings.frontend_url}/connections?connected=jira")

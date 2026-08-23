import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.rag import OAuthConnection

logger = logging.getLogger(__name__)

JIRA_TOKEN_URL = "https://auth.atlassian.com/oauth/token"


async def ensure_fresh_token(session: AsyncSession, connection: OAuthConnection) -> str:
    """Refreshes the Jira access token if expired/about to expire, persisting
    the rotated tokens. Mirrors the worker's identical helper in
    app/integrations/jira.py — duplicated because /sync/jira in this service
    also calls the Jira API directly (to enumerate issues) rather than
    routing through the worker."""
    if connection.expires_at is None:
        return connection.access_token

    now = datetime.now(timezone.utc)
    if connection.expires_at > now + timedelta(seconds=60):
        return connection.access_token

    logger.info("Refreshing Jira access token for connection %s", connection.id)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            JIRA_TOKEN_URL,
            json={
                "grant_type": "refresh_token",
                "client_id": settings.jira_client_id,
                "client_secret": settings.jira_client_secret,
                "refresh_token": connection.refresh_token,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    connection.access_token = data["access_token"]
    connection.refresh_token = data.get("refresh_token", connection.refresh_token)
    connection.expires_at = now + timedelta(seconds=data.get("expires_in", 3600))
    await session.commit()
    return connection.access_token

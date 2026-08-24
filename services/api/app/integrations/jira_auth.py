import logging
from datetime import datetime, timedelta, timezone

import httpx
from cryptography.fernet import InvalidToken
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import decrypt_token, encrypt_token
from app.models.rag import OAuthConnection

logger = logging.getLogger(__name__)

JIRA_TOKEN_URL = "https://auth.atlassian.com/oauth/token"


async def ensure_fresh_token(session: AsyncSession, connection: OAuthConnection) -> str:
    """Refreshes the Jira access token if expired/about to expire, persisting
    the rotated tokens. Mirrors the worker's identical helper in
    app/integrations/jira.py — duplicated because /sync/jira in this service
    also calls the Jira API directly (to enumerate issues) rather than
    routing through the worker."""
    # connection.access_token/refresh_token are ciphertext at rest (see
    # app/core/crypto.py). A row written before encryption landed (or otherwise
    # corrupted) raises InvalidToken here — fail the request cleanly rather than
    # let a raw decrypt error surface as an unhandled 500. This is also the only
    # decrypt in the refresh path, so both branches below reuse its plaintext
    # result instead of re-reading connection.access_token/.refresh_token.
    try:
        access_token = decrypt_token(connection.access_token)
        refresh_token = decrypt_token(connection.refresh_token)
    except InvalidToken:
        raise HTTPException(
            status_code=400,
            detail="This connection's stored credentials are no longer valid — please reconnect",
        )

    if connection.expires_at is None:
        return access_token

    now = datetime.now(timezone.utc)
    if connection.expires_at > now + timedelta(seconds=60):
        return access_token

    logger.info("Refreshing Jira access token for connection %s", connection.id)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            JIRA_TOKEN_URL,
            json={
                "grant_type": "refresh_token",
                "client_id": settings.jira_client_id,
                "client_secret": settings.jira_client_secret,
                "refresh_token": refresh_token,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    new_access_token = data["access_token"]
    new_refresh_token = data.get("refresh_token", refresh_token)
    connection.access_token = encrypt_token(new_access_token)
    connection.refresh_token = encrypt_token(new_refresh_token)
    connection.expires_at = now + timedelta(seconds=data.get("expires_in", 3600))
    await session.commit()
    return new_access_token

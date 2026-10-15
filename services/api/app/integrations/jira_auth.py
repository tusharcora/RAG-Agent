import logging
from datetime import datetime, timedelta, timezone

import httpx
from cryptography.fernet import InvalidToken
from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import decrypt_token, encrypt_token
from app.integrations import broker
from app.integrations.connection_state import disconnected_detail, require_connected
from app.models.rag import OAuthConnection

logger = logging.getLogger(__name__)

JIRA_TOKEN_URL = "https://auth.atlassian.com/oauth/token"
RECONNECT = "Jira needs reconnecting. Use Reconnect on the Connections page."

async def _mark_needs_reconnect(session: AsyncSession, connection: OAuthConnection) -> None:
    """Persists the sign-in Worker's refusal of this connection's refresh token, so GET /connections shows
    Reconnect even when the refusal happened before any sync event existed (Sync now, auto_sync, preview).
    Same conditional write as the token update below: the UPDATE row-locks and re-checks the row, so a
    connection disconnected meanwhile is left alone (Disconnect clears the flag)."""
    now = datetime.now(timezone.utc)
    result = await session.execute(
        update(OAuthConnection)
        .where(OAuthConnection.id == connection.id, OAuthConnection.disconnected_at.is_(None))
        .values(needs_reconnect_at=now)
        .returning(OAuthConnection.id)
        .execution_options(synchronize_session=False)
    )
    if result.first() is not None:
        connection.needs_reconnect_at = now
    await session.commit()


async def ensure_fresh_token(session: AsyncSession, connection: OAuthConnection) -> str:
    """Refreshes the Jira access token if expired/about to expire, persisting
    the rotated tokens. Mirrors the worker's identical helper in
    app/integrations/jira.py — duplicated because /sync/jira in this service
    also calls the Jira API directly (to enumerate issues) rather than
    routing through the worker."""
    # Disconnected (tokens wiped): nothing to refresh, and refreshing would bring the tokens back.
    require_connected(connection, "jira")
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
    if settings.app_mode == "desktop":
        # The desktop app has no OAuth client secret; the sign-in Worker holds it.
        try:
            data = await broker.refresh_jira(refresh_token)
        except broker.BrokerRefused:
            await _mark_needs_reconnect(session, connection)
            raise HTTPException(status_code=400, detail=RECONNECT)
        except broker.BrokerUnavailable:
            raise HTTPException(status_code=502, detail=broker.UNREACHABLE)
    else:
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
    values = {
        "access_token": encrypt_token(new_access_token),
        "refresh_token": encrypt_token(new_refresh_token),
        "expires_at": now + timedelta(seconds=data.get("expires_in", 3600)),
        # Any successful refresh clears a needs-reconnect flag (a false positive clears itself).
        "needs_reconnect_at": None,
    }
    # Write only if still connected: a disconnect that committed while this refresh
    # was in flight must not get live tokens written back onto its wiped row. The
    # UPDATE also row-locks until commit, so a disconnect arriving now waits and
    # then wipes what was written. Same guard as the worker's _ensure_fresh_token.
    result = await session.execute(
        update(OAuthConnection)
        .where(OAuthConnection.id == connection.id, OAuthConnection.disconnected_at.is_(None))
        .values(**values)
        .returning(OAuthConnection.id)
        .execution_options(synchronize_session=False)
    )
    if result.first() is None:
        await session.rollback()
        raise HTTPException(status_code=409, detail=disconnected_detail("jira"))
    for key, value in values.items():
        setattr(connection, key, value)
    await session.commit()
    return new_access_token

"""Saving Notion/Jira connections, shared by the server-mode OAuth callbacks
(notion_oauth.py / jira_oauth.py) and the desktop Connect flow (connect.py)."""

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import encrypt_token
from app.integrations.github_state import reset_github_state
from app.models.rag import Document, OAuthConnection

logger = logging.getLogger(__name__)


async def _reuse_row(session: AsyncSession, connection: OAuthConnection, new_workspace_id: str) -> None:
    """Reconnecting reuses the org's existing row (UNIQUE(org_id, provider)) and
    clears its disconnected marker. Documents it still holds (kept at disconnect,
    or synced before a reconnect) stay only when the new workspace is the same one;
    another workspace's pages/issues would otherwise sit beside the new ones and
    never be updated or pruned, so they're deleted (chunks go by cascade)."""
    if connection.workspace_id != new_workspace_id:
        result = await session.execute(
            delete(Document).where(Document.connection_id == connection.id).returning(Document.id)
        )
        removed = len(result.all())
        await reset_github_state(session, connection.id)
        if removed:
            logger.info(
                "Reconnected %s to a different workspace: removed %d old document(s)", connection.provider, removed
            )
    connection.disconnected_at = None
    # A fresh grant: whatever refresh token was refused is replaced.
    connection.needs_reconnect_at = None


async def save_notion_connection(session: AsyncSession, org_id: uuid.UUID, data: dict) -> OAuthConnection:
    """`data` is Notion's token response: access_token, workspace_id, workspace_name, bot_id."""
    result = await session.execute(
        select(OAuthConnection).where(OAuthConnection.provider == "notion", OAuthConnection.org_id == org_id)
    )
    connection = result.scalar_one_or_none()
    workspace_id = data.get("workspace_id") or ""
    if connection is None:
        # UNIQUE(org_id, provider): reconnecting overwrites this org's prior connection.
        connection = OAuthConnection(id=uuid.uuid4(), org_id=org_id, provider="notion", access_token="")
        session.add(connection)
    else:
        await _reuse_row(session, connection, workspace_id)

    connection.workspace_id = workspace_id
    connection.workspace_name = data.get("workspace_name") or "Notion workspace"
    connection.access_token = encrypt_token(data["access_token"])
    connection.bot_id = data.get("bot_id")
    connection.refresh_token = None  # Notion tokens don't expire
    connection.expires_at = None
    connection.updated_at = datetime.now(timezone.utc)
    await session.commit()

    logger.info("Notion connected: workspace=%s", connection.workspace_name)
    return connection


async def save_jira_connection(
    session: AsyncSession, org_id: uuid.UUID, token_data: dict, site: dict
) -> OAuthConnection:
    """`token_data` is Atlassian's token response (access_token, refresh_token,
    expires_in); `site` is one accessible-resources entry (id, url, name)."""
    now = datetime.now(timezone.utc)
    result = await session.execute(
        select(OAuthConnection).where(OAuthConnection.provider == "jira", OAuthConnection.org_id == org_id)
    )
    connection = result.scalar_one_or_none()
    if connection is None:
        connection = OAuthConnection(id=uuid.uuid4(), org_id=org_id, provider="jira", access_token="")
        session.add(connection)
    else:
        await _reuse_row(session, connection, site["id"])

    connection.workspace_id = site["id"]  # Jira Cloud id, used in api.atlassian.com/ex/jira/{id}/... calls
    connection.workspace_name = site.get("name") or site["url"]
    connection.site_url = site["url"]
    connection.access_token = encrypt_token(token_data["access_token"])
    connection.refresh_token = encrypt_token(token_data.get("refresh_token"))
    connection.expires_at = now + timedelta(seconds=token_data.get("expires_in", 3600))
    connection.bot_id = None
    connection.updated_at = now
    await session.commit()

    logger.info("Jira connected: site=%s", connection.workspace_name)
    return connection

"""Saving Notion/Jira connections, shared by the server-mode OAuth callbacks
(notion_oauth.py / jira_oauth.py) and the desktop Connect flow (connect.py)."""

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import encrypt_token
from app.integrations.jira_auth import clear_needs_reconnect
from app.models.rag import OAuthConnection

logger = logging.getLogger(__name__)


async def save_notion_connection(session: AsyncSession, org_id: uuid.UUID, data: dict) -> OAuthConnection:
    """`data` is Notion's token response: access_token, workspace_id, workspace_name, bot_id."""
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

    connection.workspace_id = site["id"]  # Jira Cloud id, used in api.atlassian.com/ex/jira/{id}/... calls
    connection.workspace_name = site.get("name") or site["url"]
    connection.site_url = site["url"]
    connection.access_token = encrypt_token(token_data["access_token"])
    connection.refresh_token = encrypt_token(token_data.get("refresh_token"))
    connection.expires_at = now + timedelta(seconds=token_data.get("expires_in", 3600))
    connection.bot_id = None
    connection.updated_at = now
    await session.commit()
    clear_needs_reconnect(connection.id)

    logger.info("Jira connected: site=%s", connection.workspace_name)
    return connection

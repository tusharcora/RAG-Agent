import logging
from datetime import datetime, timedelta, timezone

import httpx
from cryptography.fernet import InvalidToken
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.crypto import decrypt_token, encrypt_token
from app.core.db import get_session
from app.models.rag import OAuthConnection

logger = logging.getLogger(__name__)

JIRA_TOKEN_URL = "https://auth.atlassian.com/oauth/token"
JIRA_API_BASE = "https://api.atlassian.com/ex/jira"


class JiraRateLimited(Exception):
    def __init__(self, retry_after: float):
        self.retry_after = retry_after
        super().__init__(f"Jira rate limited, retry after {retry_after}s")


def _jira_wait(retry_state):
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if isinstance(exc, JiraRateLimited):
        return exc.retry_after
    return wait_exponential(multiplier=1, min=1, max=30)(retry_state)


@retry(retry=retry_if_exception_type(JiraRateLimited), wait=_jira_wait, stop=stop_after_attempt(5), reraise=True)
async def _request(client: httpx.AsyncClient, method: str, url: str, **kwargs) -> httpx.Response:
    resp = await client.request(method, url, **kwargs)
    if resp.status_code == 429:
        raise JiraRateLimited(retry_after=float(resp.headers.get("Retry-After", 1)))
    resp.raise_for_status()
    return resp


def _adf_to_text(adf: dict | None) -> str:
    """Jira descriptions/comments are Atlassian Document Format (structured
    JSON), not plain text — walk it and pull out the text runs."""
    if not adf:
        return ""
    parts: list[str] = []

    def walk(node: dict) -> None:
        if node.get("type") == "text":
            parts.append(node.get("text", ""))
        for child in node.get("content", []) or []:
            walk(child)
        if node.get("type") in ("paragraph", "heading"):
            parts.append("\n\n")

    walk(adf)
    return "".join(parts).strip()


async def _ensure_fresh_token(connection: OAuthConnection) -> str:
    """Jira access tokens expire (~1h). Refreshes via Atlassian's
    refresh_token grant if expired or about to expire, persisting the
    rotated tokens back to oauth_connections. Without this, Jira ingestion
    silently starts failing about an hour after connecting."""
    # connection.access_token/refresh_token are ciphertext at rest (see
    # app/core/crypto.py). A row written before encryption landed (or otherwise
    # corrupted) raises InvalidToken here — this can't return an HTTP response
    # (worker, not the api), so log clearly and let it propagate: main.py's
    # handle_message treats it like any other handler failure (retry, then
    # dead-letter after max_retries).
    try:
        access_token = decrypt_token(connection.access_token)
        refresh_token = decrypt_token(connection.refresh_token)
    except InvalidToken:
        logger.error(
            "connection_id=%s has undecryptable stored credentials (pre-encryption plaintext, "
            "or corrupted) — reconnect required",
            connection.id,
        )
        raise

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
    new_refresh_token = data.get("refresh_token", refresh_token)  # Atlassian rotates refresh tokens
    new_expires_at = now + timedelta(seconds=data.get("expires_in", 3600))

    async with get_session() as session:
        db_connection = await session.get(OAuthConnection, connection.id)
        db_connection.access_token = encrypt_token(new_access_token)
        db_connection.refresh_token = encrypt_token(new_refresh_token)
        db_connection.expires_at = new_expires_at
        await session.commit()

    # Keep the in-memory connection's fields ciphertext, matching the DB — every
    # call into this function decrypts them fresh at the top, so a stray plaintext
    # value here would only be safe until the *next* call re-decrypts it.
    connection.access_token = encrypt_token(new_access_token)
    connection.refresh_token = encrypt_token(new_refresh_token)
    connection.expires_at = new_expires_at
    return new_access_token


class JiraClient:
    def __init__(self, connection: OAuthConnection):
        self._connection = connection

    async def _headers(self) -> dict:
        token = await _ensure_fresh_token(self._connection)
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    async def fetch_issue(self, issue_key: str) -> dict:
        headers = await self._headers()
        cloud_id = self._connection.workspace_id
        base = f"{JIRA_API_BASE}/{cloud_id}/rest/api/3"
        async with httpx.AsyncClient(timeout=30) as client:
            issue_resp = await _request(client, "GET", f"{base}/issue/{issue_key}", headers=headers)
            comments_resp = await _request(client, "GET", f"{base}/issue/{issue_key}/comment", headers=headers)

        fields = issue_resp.json().get("fields", {})
        comments_data = comments_resp.json()

        title = fields.get("summary", "Untitled")
        description = _adf_to_text(fields.get("description"))
        comments = [_adf_to_text(c.get("body")) for c in comments_data.get("comments", [])]

        return {
            "title": title,
            "description": description,
            "comments": [c for c in comments if c],
            "url": f"{self._connection.site_url}/browse/{issue_key}",
            "updated": fields.get("updated"),
        }

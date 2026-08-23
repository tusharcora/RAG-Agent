import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.auth import AuthContext, require_auth_or_service_token
from app.core.config import settings
from app.core.db import get_session
from app.core.queue import publish_event
from app.core.ratelimit import rate_limiter
from app.integrations.jira_auth import ensure_fresh_token
from app.models.rag import OAuthConnection

logger = logging.getLogger(__name__)

router = APIRouter()

NOTION_SEARCH_URL = "https://api.notion.com/v1/search"
JIRA_SEARCH_URL_TMPL = "https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/search"


class _RateLimited(Exception):
    def __init__(self, retry_after: float):
        self.retry_after = retry_after


def _wait(retry_state):
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if isinstance(exc, _RateLimited):
        return exc.retry_after
    return wait_exponential(multiplier=1, min=1, max=30)(retry_state)


@retry(retry=retry_if_exception_type(_RateLimited), wait=_wait, stop=stop_after_attempt(5), reraise=True)
async def _request(client: httpx.AsyncClient, method: str, url: str, **kwargs) -> httpx.Response:
    resp = await client.request(method, url, **kwargs)
    if resp.status_code == 429:
        raise _RateLimited(retry_after=float(resp.headers.get("Retry-After", 1)))
    resp.raise_for_status()
    return resp


async def _get_connection(session: AsyncSession, provider: str, org_id) -> OAuthConnection:
    result = await session.execute(
        select(OAuthConnection).where(OAuthConnection.provider == provider, OAuthConnection.org_id == org_id)
    )
    connection = result.scalar_one_or_none()
    if connection is None:
        raise HTTPException(
            status_code=400, detail=f"No {provider} connection — visit /oauth/{provider}/authorize first"
        )
    return connection


@router.post("/notion", dependencies=[Depends(rate_limiter("sync_notion", settings.rate_limit_sync_per_minute))])
async def sync_notion(
    auth: AuthContext = Depends(require_auth_or_service_token), session: AsyncSession = Depends(get_session)
) -> dict:
    """Enumerates all pages via Notion's /search and publishes one
    rag.notion_page_updated event per page — no embedding work happens here,
    it's all handled by the worker off the queue."""
    connection = await _get_connection(session, "notion", auth.org_id)

    published = 0
    truncated = False
    cursor: str | None = None
    headers = {
        "Authorization": f"Bearer {connection.access_token}",
        "Notion-Version": settings.notion_api_version,
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            body: dict = {"filter": {"property": "object", "value": "page"}, "page_size": 100}
            if cursor:
                body["start_cursor"] = cursor
            resp = await _request(client, "POST", NOTION_SEARCH_URL, headers=headers, json=body)
            data = resp.json()

            for page in data.get("results", []):
                if published >= settings.max_pages_per_sync:
                    truncated = True
                    break
                await publish_event(
                    session,
                    routing_key="rag.notion_page_updated",
                    payload={
                        "page_id": page["id"],
                        "connection_id": str(connection.id),
                        "org_id": str(connection.org_id),
                    },
                    # Scoped by connection_id (unique per org+provider), not just page_id —
                    # otherwise two orgs' page ids could collide in the global-namespaced
                    # dedupe Redis key and one org's event would be dropped as a false
                    # "duplicate" of another's.
                    dedupe_key=f"notion:{connection.id}:{page['id']}:{page.get('last_edited_time', '')}",
                    org_id=connection.org_id,
                )
                published += 1

            if truncated or not data.get("has_more"):
                break
            cursor = data.get("next_cursor")

    logger.info("Notion sync published=%d truncated=%s", published, truncated)
    return {"published": published, "truncated": truncated}


@router.post("/jira", dependencies=[Depends(rate_limiter("sync_jira", settings.rate_limit_sync_per_minute))])
async def sync_jira(
    auth: AuthContext = Depends(require_auth_or_service_token), session: AsyncSession = Depends(get_session)
) -> dict:
    """Enumerates issues via Jira's /search (JQL) and publishes one
    rag.jira_issue_updated event per issue."""
    connection = await _get_connection(session, "jira", auth.org_id)
    access_token = await ensure_fresh_token(session, connection)

    published = 0
    truncated = False
    start_at = 0
    page_size = 100
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    url = JIRA_SEARCH_URL_TMPL.format(cloud_id=connection.workspace_id)

    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            params = {"jql": "ORDER BY updated DESC", "startAt": start_at, "maxResults": page_size}
            resp = await _request(client, "GET", url, headers=headers, params=params)
            data = resp.json()

            issues = data.get("issues", [])
            for issue in issues:
                if published >= settings.max_issues_per_sync:
                    truncated = True
                    break
                await publish_event(
                    session,
                    routing_key="rag.jira_issue_updated",
                    payload={
                        "issue_id": issue["id"],
                        "issue_key": issue["key"],
                        "connection_id": str(connection.id),
                        "org_id": str(connection.org_id),
                    },
                    # See sync_notion's dedupe_key comment — Jira issue ids are small ints
                    # local to one Jira site and WILL collide across two orgs' sites.
                    dedupe_key=f"jira:{connection.id}:{issue['id']}:{issue.get('fields', {}).get('updated', '')}",
                    org_id=connection.org_id,
                )
                published += 1

            start_at += page_size
            if truncated or not issues or start_at >= data.get("total", 0):
                break

    logger.info("Jira sync published=%d truncated=%s", published, truncated)
    return {"published": published, "truncated": truncated}

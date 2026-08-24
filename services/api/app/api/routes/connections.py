import uuid
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.sync import JIRA_SEARCH_URL_TMPL, NOTION_SEARCH_URL, _request
from app.core.auth import AuthContext, require_auth, require_role
from app.core.config import settings
from app.core.db import get_session
from app.integrations.jira_auth import ensure_fresh_token
from app.models.event_log import EventLog
from app.models.rag import ConnectionMember, Document, OAuthConnection

router = APIRouter(dependencies=[Depends(require_auth)])

PROVIDERS = ("notion", "jira")


class ConnectionStatus(BaseModel):
    id: str | None
    provider: str
    connected: bool
    workspace_name: str | None
    site_url: str | None
    last_synced_at: datetime | None
    visibility_mode: str | None
    dead_lettered_count_24h: int
    last_sync_status: str | None  # most recent event_log row's status for this connection, or None if it has never synced


@router.get("")
async def connections(
    auth: AuthContext = Depends(require_auth), session: AsyncSession = Depends(get_session)
) -> list[ConnectionStatus]:
    out: list[ConnectionStatus] = []
    for provider in PROVIDERS:
        result = await session.execute(
            select(OAuthConnection).where(OAuthConnection.provider == provider, OAuthConnection.org_id == auth.org_id)
        )
        conn = result.scalar_one_or_none()

        if conn is None:
            out.append(
                ConnectionStatus(
                    id=None,
                    provider=provider,
                    connected=False,
                    workspace_name=None,
                    site_url=None,
                    last_synced_at=None,
                    visibility_mode=None,
                    dead_lettered_count_24h=0,
                    last_sync_status=None,
                )
            )
            continue

        # No dedicated "last synced" column — embed.py updates documents.synced_at
        # on every sync (even no-op content-unchanged skips), so MAX() here is an
        # accurate live signal without a redundant field that could drift.
        last_synced_result = await session.execute(
            select(func.max(Document.synced_at)).where(Document.connection_id == conn.id)
        )
        last_synced_at = last_synced_result.scalar_one()

        dead_lettered_count_24h, last_sync_status = await _sync_health(session, auth.org_id, conn.id)

        out.append(
            ConnectionStatus(
                id=str(conn.id),
                provider=provider,
                connected=True,
                workspace_name=conn.workspace_name,
                site_url=conn.site_url,
                last_synced_at=last_synced_at,
                visibility_mode=conn.visibility_mode,
                dead_lettered_count_24h=dead_lettered_count_24h,
                last_sync_status=last_sync_status,
            )
        )
    return out


async def _sync_health(session: AsyncSession, org_id: uuid.UUID, connection_id: uuid.UUID) -> tuple[int, str | None]:
    """Dead-letter count + most recent event status for one connection.

    event_log has no connection_id column (it's shared across both projects,
    and codereview.* events will never have one) — connection_id only lives
    inside payload, so this reaches into the JSONB via ->>'connection_id'.
    Both figures come out of a single round trip (two scalar subqueries in
    one SELECT) rather than two separate queries, since this runs once per
    connection on every /connections call (including the polled ones).
    """
    conn_filter = (
        (EventLog.org_id == org_id)
        & (EventLog.routing_key.like("rag.%"))
        & (EventLog.payload["connection_id"].astext == str(connection_id))
    )
    since = datetime.now(timezone.utc) - timedelta(hours=24)

    count_sq = (
        select(func.count())
        .where(conn_filter, EventLog.status == "dead_lettered", EventLog.created_at > since)
        .scalar_subquery()
    )
    last_status_sq = (
        select(EventLog.status).where(conn_filter).order_by(EventLog.created_at.desc()).limit(1).scalar_subquery()
    )

    result = await session.execute(select(count_sq, last_status_sq))
    dead_lettered_count, last_status = result.one()
    return dead_lettered_count, last_status


async def _get_org_connection(session: AsyncSession, connection_id: uuid.UUID, org_id: uuid.UUID) -> OAuthConnection:
    result = await session.execute(
        select(OAuthConnection).where(OAuthConnection.id == connection_id, OAuthConnection.org_id == org_id)
    )
    connection = result.scalar_one_or_none()
    if connection is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    return connection


class SetVisibilityRequest(BaseModel):
    mode: str  # 'org_wide' | 'restricted'


@router.patch("/{connection_id}/visibility")
async def set_visibility(
    connection_id: uuid.UUID,
    request: SetVisibilityRequest,
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> ConnectionStatus:
    require_role(auth, "owner", "admin")
    if request.mode not in ("org_wide", "restricted"):
        raise HTTPException(status_code=400, detail="mode must be 'org_wide' or 'restricted'")

    connection = await _get_org_connection(session, connection_id, auth.org_id)
    connection.visibility_mode = request.mode
    await session.commit()

    return ConnectionStatus(
        id=str(connection.id),
        provider=connection.provider,
        connected=True,
        workspace_name=connection.workspace_name,
        site_url=connection.site_url,
        last_synced_at=None,
        visibility_mode=connection.visibility_mode,
        # Not recomputed here — the caller only wants confirmation the mode
        # changed, and GET /connections (polled) will refresh these shortly.
        dead_lettered_count_24h=0,
        last_sync_status=None,
    )


class SetMembersRequest(BaseModel):
    user_ids: list[uuid.UUID]


@router.put("/{connection_id}/members", status_code=204)
async def set_members(
    connection_id: uuid.UUID,
    request: SetMembersRequest,
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> None:
    require_role(auth, "owner", "admin")
    await _get_org_connection(session, connection_id, auth.org_id)

    # Replace the allow-list wholesale — simpler and safer than diffing
    # add/remove for what's expected to be a small, infrequently-changed list.
    await session.execute(delete(ConnectionMember).where(ConnectionMember.connection_id == connection_id))
    for user_id in set(request.user_ids):
        session.add(ConnectionMember(id=uuid.uuid4(), connection_id=connection_id, user_id=user_id))
    await session.commit()


@router.get("/{connection_id}/members")
async def get_members(
    connection_id: uuid.UUID,
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> list[str]:
    require_role(auth, "owner", "admin")
    await _get_org_connection(session, connection_id, auth.org_id)

    result = await session.execute(
        select(ConnectionMember.user_id).where(ConnectionMember.connection_id == connection_id)
    )
    return [str(uid) for uid in result.scalars().all()]


class ConnectionPreview(BaseModel):
    visible_count: int
    truncated: bool  # true = "at least this many" (an exact total wasn't available in one call)


@router.get("/{connection_id}/preview")
async def preview(
    connection_id: uuid.UUID,
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> ConnectionPreview:
    """How many pages/issues this connection can currently see, in a single
    cheap API call — no pagination loop, no event publishing. This exists
    specifically to answer "why didn't my page sync?" *before* a real sync
    runs: Notion/Jira only expose content that's been explicitly shared with
    the integration, which is easy to get wrong during OAuth and has no
    other visible signal until a sync silently comes back near-empty.

    Deliberately a separate endpoint rather than a field on GET /connections
    — that list is polled by the frontend, and folding a live Notion/Jira
    API call into every poll would make an already-frequent request slow
    and rate-limit-hungry for no benefit; the frontend fetches this lazily,
    once, when a connection card first renders as connected.
    """
    connection = await _get_org_connection(session, connection_id, auth.org_id)

    async with httpx.AsyncClient(timeout=15) as client:
        if connection.provider == "notion":
            headers = {
                "Authorization": f"Bearer {connection.access_token}",
                "Notion-Version": settings.notion_api_version,
                "Content-Type": "application/json",
            }
            # page_size capped well below max_pages_per_sync — this is a
            # preview, not an enumeration. has_more=True only tells us "at
            # least this many," never an exact total; Notion's /search does
            # not return one.
            resp = await _request(
                client,
                "POST",
                NOTION_SEARCH_URL,
                headers=headers,
                json={"filter": {"property": "object", "value": "page"}, "page_size": 25},
            )
            data = resp.json()
            results = data.get("results", [])
            return ConnectionPreview(visible_count=len(results), truncated=bool(data.get("has_more")))

        elif connection.provider == "jira":
            access_token = await ensure_fresh_token(session, connection)
            headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
            url = JIRA_SEARCH_URL_TMPL.format(cloud_id=connection.workspace_id)
            # maxResults=1 is enough — unlike Notion, Jira's /search always
            # returns an exact "total" regardless of page size, so this is
            # both cheap and exact (no has_more-style ambiguity).
            resp = await _request(
                client, "GET", url, headers=headers, params={"jql": "ORDER BY updated DESC", "maxResults": 1}
            )
            data = resp.json()
            total = int(data.get("total", 0))
            return ConnectionPreview(visible_count=total, truncated=False)

        raise HTTPException(status_code=400, detail=f"Unknown provider: {connection.provider}")

import uuid
from urllib.parse import quote
from datetime import datetime, timedelta, timezone

import httpx
from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.sync import JIRA_SEARCH_URL_TMPL, NOTION_SEARCH_URL, _request
from app.core.auth import AuthContext, require_auth, require_role
from app.core.config import settings
from app.core.crypto import decrypt_token, encrypt_token
from app.core.db import get_session
from app.integrations.connection_state import require_connected
from app.integrations.connections import save_notion_connection
from app.integrations.github_state import reset_github_state
from app.integrations.jira_auth import ensure_fresh_token
from app.models.event_log import EventLog
from app.models.rag import ConnectionMember, Document, GithubRepo, OAuthConnection

router = APIRouter(dependencies=[Depends(require_auth)])

PROVIDERS = ("notion", "jira", "github")


class GithubInfo(BaseModel):
    repo_count: int
    repo_names: list[str]
    # Where the person chooses which repositories the app can see; "" when the app slug is unknown.
    choose_url: str


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
    # The provider refused this connection's refresh token (oauth_connections.needs_reconnect_at, set by the
    # Jira/GitHub refresh paths; cleared by a successful refresh, a reconnect, or Disconnect).
    needs_reconnect: bool = False
    # Disconnected (POST /{id}/disconnect): connected is False, the row and id remain, and
    # document_count says how many documents were kept (still searchable, never updated).
    disconnected: bool = False
    document_count: int = 0
    github: GithubInfo | None = None  # only on a connected GitHub row


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

        document_count = (
            await session.execute(select(func.count(Document.id)).where(Document.connection_id == conn.id))
        ).scalar_one()

        if conn.disconnected_at is not None:
            out.append(
                ConnectionStatus(
                    id=str(conn.id),
                    provider=provider,
                    connected=False,
                    workspace_name=conn.workspace_name,
                    site_url=conn.site_url,
                    last_synced_at=None,
                    visibility_mode=conn.visibility_mode,
                    dead_lettered_count_24h=0,
                    last_sync_status=None,
                    disconnected=True,
                    document_count=document_count,
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

        github = None
        if provider == "github":
            names = (
                (
                    await session.execute(
                        select(GithubRepo.full_name).where(GithubRepo.connection_id == conn.id).order_by(GithubRepo.full_name)
                    )
                )
                .scalars()
                .all()
            )
            slug = conn.bot_id or ""
            github = GithubInfo(
                repo_count=len(names),
                repo_names=list(names),
                choose_url=f"https://github.com/apps/{quote(slug, safe='')}/installations/new" if slug else "",
            )

        out.append(
            ConnectionStatus(
                id=str(conn.id),
                provider=provider,
                connected=True,
                github=github,
                workspace_name=conn.workspace_name,
                site_url=conn.site_url,
                last_synced_at=last_synced_at,
                visibility_mode=conn.visibility_mode,
                dead_lettered_count_24h=dead_lettered_count_24h,
                last_sync_status=last_sync_status,
                needs_reconnect=conn.needs_reconnect_at is not None,
                document_count=document_count,
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


class DisconnectRequest(BaseModel):
    remove_documents: bool


async def _delete_documents(session: AsyncSession, connection_id: uuid.UUID) -> int:
    # Chunks go with them (chunks.document_id ON DELETE CASCADE).
    result = await session.execute(
        delete(Document).where(Document.connection_id == connection_id).returning(Document.id)
    )
    removed = len(result.all())
    await reset_github_state(session, connection_id)
    return removed


@router.post("/{connection_id}/disconnect")
async def disconnect(
    connection_id: uuid.UUID,
    request: DisconnectRequest,
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Wipes the stored tokens and marks the connection disconnected, which stops
    every sync path (Sync now, auto_sync, worker events already queued). The row
    stays so a reconnect can reuse it and so kept documents still have a parent.
    Idempotent: a repeat keeps the first disconnected_at, and remove_documents
    still removes whatever was kept."""
    require_role(auth, "owner", "admin")
    connection = await _get_org_connection(session, connection_id, auth.org_id)

    removed = await _delete_documents(session, connection.id) if request.remove_documents else 0
    connection.access_token = None
    connection.refresh_token = None
    connection.expires_at = None
    connection.needs_reconnect_at = None
    if connection.disconnected_at is None:
        connection.disconnected_at = datetime.now(timezone.utc)
    connection.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return {"disconnected": True, "removed": removed}


@router.post("/{connection_id}/remove-documents")
async def remove_documents(
    connection_id: uuid.UUID,
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Deletes the documents a disconnected connection kept. A live connection's
    documents are managed by sync, so this refuses (409) while connected."""
    require_role(auth, "owner", "admin")
    connection = await _get_org_connection(session, connection_id, auth.org_id)
    if connection.disconnected_at is None:
        raise HTTPException(status_code=409, detail="Disconnect this connection before removing its documents.")
    removed = await _delete_documents(session, connection.id)
    await session.commit()
    return {"removed": removed}


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
    require_connected(connection)

    async with httpx.AsyncClient(timeout=15) as client:
        if connection.provider == "notion":
            try:
                access_token = decrypt_token(connection.access_token)
            except InvalidToken:
                raise HTTPException(
                    status_code=400,
                    detail="This connection's stored credentials are no longer valid — please reconnect",
                )
            headers = {
                "Authorization": f"Bearer {access_token}",
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


class NotionTokenRequest(BaseModel):
    token: str


def _desktop_only() -> None:
    if settings.app_mode != "desktop":
        raise HTTPException(status_code=404, detail="Not found")


# Desktop only: server deployments keep the documented OAuth-only model, so an
# org admin there can't swap an OAuth connection for an internal-integration
# secret.
@router.post("/notion/token", dependencies=[Depends(_desktop_only)])
async def connect_notion_with_token(
    request: NotionTokenRequest,
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Connects Notion with an internal integration secret instead of OAuth.
    The desktop app has no public address for an OAuth redirect, so the user
    creates an internal integration at notion.so/profile/integrations, shares
    pages with it, and pastes its secret here. Sync then works exactly as for
    an OAuth connection (both are bearer tokens to the same API)."""
    require_role(auth, "owner", "admin")
    token = request.token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="Paste the integration secret.")

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                "https://api.notion.com/v1/users/me",
                headers={"Authorization": f"Bearer {token}", "Notion-Version": settings.notion_api_version},
            )
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Couldn't reach Notion. Check your internet connection.")
    if resp.status_code == 401:
        raise HTTPException(status_code=400, detail="Notion rejected that secret. Copy it again from notion.so/profile/integrations.")
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Notion returned {resp.status_code}.")
    bot = resp.json().get("bot") or {}

    connection = await save_notion_connection(
        session,
        auth.org_id,
        {
            "access_token": token,
            "workspace_id": bot.get("workspace_id") or "",
            "workspace_name": bot.get("workspace_name"),
            "bot_id": resp.json().get("id"),
        },
    )
    return {"connected": True, "workspace_name": connection.workspace_name}

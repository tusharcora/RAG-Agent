import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthContext, require_auth, require_role
from app.core.db import get_session
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

        out.append(
            ConnectionStatus(
                id=str(conn.id),
                provider=provider,
                connected=True,
                workspace_name=conn.workspace_name,
                site_url=conn.site_url,
                last_synced_at=last_synced_at,
                visibility_mode=conn.visibility_mode,
            )
        )
    return out


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

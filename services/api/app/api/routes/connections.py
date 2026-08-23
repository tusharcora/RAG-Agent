from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_api_key
from app.core.db import get_session
from app.models.rag import Document, OAuthConnection

router = APIRouter(dependencies=[Depends(require_api_key)])

PROVIDERS = ("notion", "jira")


class ConnectionStatus(BaseModel):
    provider: str
    connected: bool
    workspace_name: str | None
    site_url: str | None
    last_synced_at: datetime | None


@router.get("")
async def connections(session: AsyncSession = Depends(get_session)) -> list[ConnectionStatus]:
    out: list[ConnectionStatus] = []
    for provider in PROVIDERS:
        result = await session.execute(select(OAuthConnection).where(OAuthConnection.provider == provider))
        conn = result.scalar_one_or_none()

        if conn is None:
            out.append(
                ConnectionStatus(provider=provider, connected=False, workspace_name=None, site_url=None, last_synced_at=None)
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
                provider=provider,
                connected=True,
                workspace_name=conn.workspace_name,
                site_url=conn.site_url,
                last_synced_at=last_synced_at,
            )
        )
    return out

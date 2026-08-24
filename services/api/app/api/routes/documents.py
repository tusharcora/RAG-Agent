import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import websearch_to_tsquery
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthContext, require_auth, require_role
from app.core.db import get_session
from app.models.rag import Chunk, ConnectionMember, Document, OAuthConnection

router = APIRouter(dependencies=[Depends(require_auth)])


class DocumentSummary(BaseModel):
    id: str
    source: str
    title: str
    url: str
    last_edited_at: datetime | None
    synced_at: datetime
    chunk_count: int
    excluded_from_retrieval: bool


class DocumentListResponse(BaseModel):
    items: list[DocumentSummary]
    total: int


class ChunkOut(BaseModel):
    id: str
    chunk_index: int
    content: str
    token_count: int | None
    metadata: dict


class DocumentDetail(BaseModel):
    id: str
    source: str
    title: str
    url: str
    last_edited_at: datetime | None
    synced_at: datetime
    excluded_from_retrieval: bool
    chunks: list[ChunkOut]


def _visibility_filter(auth: AuthContext):
    """Same connection-level visibility rule as query.py's retrieval filter —
    the knowledge base browser exposes the same chunk content /query does, so
    a restricted connection must be excluded here too, or a member left off
    the allow-list could just read the content directly instead of asking a
    question."""
    if auth.role in ("owner", "admin"):
        return None
    return or_(
        OAuthConnection.visibility_mode == "org_wide",
        OAuthConnection.id.in_(select(ConnectionMember.connection_id).where(ConnectionMember.user_id == auth.user_id)),
    )


@router.get("")
async def list_documents(
    source: str | None = None,
    search: str | None = None,
    limit: int = Query(25, le=100),
    offset: int = 0,
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> DocumentListResponse:
    stmt = (
        select(
            Document.id,
            Document.source,
            Document.title,
            Document.url,
            Document.last_edited_at,
            Document.synced_at,
            Document.excluded_from_retrieval,
            func.count(Chunk.id).label("chunk_count"),
        )
        .join(OAuthConnection, OAuthConnection.id == Document.connection_id)
        .outerjoin(Chunk, Chunk.document_id == Document.id)
        .where(OAuthConnection.org_id == auth.org_id)
        .group_by(Document.id)
        .limit(limit)
        .offset(offset)
    )
    count_stmt = (
        select(func.count())
        .select_from(Document)
        .join(OAuthConnection, OAuthConnection.id == Document.connection_id)
        .where(OAuthConnection.org_id == auth.org_id)
    )

    visibility = _visibility_filter(auth)
    if visibility is not None:
        stmt = stmt.where(visibility)
        count_stmt = count_stmt.where(visibility)

    if source:
        stmt = stmt.where(Document.source == source)
        count_stmt = count_stmt.where(Document.source == source)
    if search:
        # websearch_to_tsquery tolerates plain user-typed phrases (quotes,
        # "or", leading "-" to exclude a word) better than plainto_tsquery,
        # and search_vector (infra/postgres/005_document_search.sql) is a
        # GENERATED STORED column backed by a GIN index — no per-request
        # to_tsvector() cost on the documents side like ILIKE's unindexed
        # scan had.
        tsquery = websearch_to_tsquery("english", search)
        stmt = stmt.where(Document.search_vector.op("@@")(tsquery))
        stmt = stmt.order_by(func.ts_rank(Document.search_vector, tsquery).desc())
        count_stmt = count_stmt.where(Document.search_vector.op("@@")(tsquery))
    else:
        stmt = stmt.order_by(Document.synced_at.desc())

    rows = (await session.execute(stmt)).all()
    total = (await session.execute(count_stmt)).scalar_one()

    items = [
        DocumentSummary(
            id=str(r.id),
            source=r.source,
            title=r.title,
            url=r.url,
            last_edited_at=r.last_edited_at,
            synced_at=r.synced_at,
            chunk_count=r.chunk_count,
            excluded_from_retrieval=r.excluded_from_retrieval,
        )
        for r in rows
    ]
    return DocumentListResponse(items=items, total=total)


@router.get("/{document_id}")
async def document_detail(
    document_id: uuid.UUID,
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> DocumentDetail:
    stmt = (
        select(Document)
        .join(OAuthConnection, OAuthConnection.id == Document.connection_id)
        .where(Document.id == document_id, OAuthConnection.org_id == auth.org_id)
    )
    visibility = _visibility_filter(auth)
    if visibility is not None:
        stmt = stmt.where(visibility)

    document = (await session.execute(stmt)).scalar_one_or_none()
    if document is None:
        # 404 whether the row doesn't exist or just isn't visible to this
        # caller — a different status code would leak a restricted
        # document's existence.
        raise HTTPException(status_code=404, detail="Document not found")

    chunk_rows = (
        await session.execute(select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.chunk_index))
    ).scalars().all()

    return DocumentDetail(
        id=str(document.id),
        source=document.source,
        title=document.title,
        url=document.url,
        last_edited_at=document.last_edited_at,
        synced_at=document.synced_at,
        excluded_from_retrieval=document.excluded_from_retrieval,
        chunks=[
            ChunkOut(
                id=str(c.id),
                chunk_index=c.chunk_index,
                content=c.content,
                token_count=c.token_count,
                metadata=c.metadata_,
            )
            for c in chunk_rows
        ],
    )


class SetExcludedRequest(BaseModel):
    excluded: bool


@router.patch("/{document_id}/exclude")
async def set_document_excluded(
    document_id: uuid.UUID,
    request: SetExcludedRequest,
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> DocumentSummary:
    require_role(auth, "owner", "admin")

    # Same org-scoped lookup document_detail uses (join to oauth_connections,
    # require org_id match) — not the visibility allow-list, since this
    # action is already owner/admin-only. 404 whether the row doesn't exist
    # or belongs to another org, not a 403 that would leak existence.
    stmt = (
        select(Document)
        .join(OAuthConnection, OAuthConnection.id == Document.connection_id)
        .where(Document.id == document_id, OAuthConnection.org_id == auth.org_id)
    )
    document = (await session.execute(stmt)).scalar_one_or_none()
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")

    document.excluded_from_retrieval = request.excluded
    await session.commit()

    chunk_count = (
        await session.execute(select(func.count(Chunk.id)).where(Chunk.document_id == document_id))
    ).scalar_one()

    return DocumentSummary(
        id=str(document.id),
        source=document.source,
        title=document.title,
        url=document.url,
        last_edited_at=document.last_edited_at,
        synced_at=document.synced_at,
        chunk_count=chunk_count,
        excluded_from_retrieval=document.excluded_from_retrieval,
    )

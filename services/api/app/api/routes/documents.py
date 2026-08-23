import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_api_key
from app.core.db import get_session
from app.models.rag import Chunk, Document

router = APIRouter(dependencies=[Depends(require_api_key)])


class DocumentSummary(BaseModel):
    id: str
    source: str
    title: str
    url: str
    last_edited_at: datetime | None
    synced_at: datetime
    chunk_count: int


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
    chunks: list[ChunkOut]


@router.get("")
async def list_documents(
    source: str | None = None,
    search: str | None = None,
    limit: int = Query(25, le=100),
    offset: int = 0,
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
            func.count(Chunk.id).label("chunk_count"),
        )
        .outerjoin(Chunk, Chunk.document_id == Document.id)
        .group_by(Document.id)
        .order_by(Document.synced_at.desc())
        .limit(limit)
        .offset(offset)
    )
    count_stmt = select(func.count()).select_from(Document)

    if source:
        stmt = stmt.where(Document.source == source)
        count_stmt = count_stmt.where(Document.source == source)
    if search:
        stmt = stmt.where(Document.title.ilike(f"%{search}%"))
        count_stmt = count_stmt.where(Document.title.ilike(f"%{search}%"))

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
        )
        for r in rows
    ]
    return DocumentListResponse(items=items, total=total)


@router.get("/{document_id}")
async def document_detail(document_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> DocumentDetail:
    document = await session.get(Document, document_id)
    if document is None:
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

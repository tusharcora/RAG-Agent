"""Vector retrieval shared by the chat endpoint (/query) and the MCP server
(/mcp). Permission filtering happens inside the SQL, before ORDER BY ...
LIMIT, so callers always get the true top-k among chunks they may see."""

import logging

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthContext
from app.integrations.voyage import embed_texts
from app.models.rag import Chunk, ConnectionMember, Document, OAuthConnection

logger = logging.getLogger(__name__)


async def retrieve_chunks(session: AsyncSession, auth: AuthContext, question: str, top_k: int) -> list[dict]:
    [question_embedding] = await embed_texts([question], input_type="query")

    stmt = (
        select(Chunk.content, Document.title, Document.url, Document.source)
        .join(Document, Document.id == Chunk.document_id)
        .join(OAuthConnection, OAuthConnection.id == Document.connection_id)
        .where(
            Chunk.embedding.is_not(None),
            OAuthConnection.org_id == auth.org_id,
            # Documents flagged stale/wrong (documents.py PATCH .../exclude)
            # must never surface as a source.
            Document.excluded_from_retrieval.is_(False),
        )
    )
    if auth.role not in ("owner", "admin"):
        # Members see org-wide connections plus any they're explicitly
        # allow-listed on. Service tokens (MCP, automation) have no user, so
        # they only ever see org-wide connections.
        visible = [OAuthConnection.visibility_mode == "org_wide"]
        if auth.user_id is not None:
            visible.append(
                OAuthConnection.id.in_(
                    select(ConnectionMember.connection_id).where(ConnectionMember.user_id == auth.user_id)
                )
            )
        stmt = stmt.where(or_(*visible))
    stmt = stmt.order_by(Chunk.embedding.cosine_distance(question_embedding)).limit(top_k)

    result = await session.execute(stmt)
    rows = [dict(r) for r in result.mappings().all()]
    if len(rows) < top_k:
        # pgvector's HNSW walk is approximate; a selective permission filter
        # can return fewer than top_k rows. Logged so degraded recall is visible.
        logger.info("Retrieved %d/%d rows for org=%s", len(rows), top_k, auth.org_id)
    return rows

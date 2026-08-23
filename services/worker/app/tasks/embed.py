import hashlib
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select

from app.core.db import get_session
from app.core.telemetry import tracer
from app.integrations.jira import JiraClient
from app.integrations.notion import NotionClient
from app.integrations.voyage import embed_texts
from app.models.rag import Chunk, Document, OAuthConnection
from app.tasks.chunking import chunk_text

logger = logging.getLogger(__name__)


def _parse_datetime(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


async def _sync_document(
    session,
    *,
    source: str,
    external_id: str,
    connection_id: uuid.UUID,
    title: str,
    url: str,
    last_edited_at,
    sections: list[tuple[str, dict]],
) -> None:
    """Upserts `documents`, then re-chunks + re-embeds `chunks` unless the
    combined content is byte-identical to what's already stored (defensive
    correctness check layered on top of the TTL-bound dedupe-key skip in
    main.py — that check only prevents re-processing the *same delivered
    event* twice; this one prevents re-embedding unchanged content on a
    genuinely new sync). `sections` lets a caller (e.g. Jira) chunk different
    parts of a document independently with different metadata tags.
    """
    combined_content = "\n\n".join(text for text, _ in sections if text)
    content_hash = hashlib.sha256(combined_content.encode()).hexdigest()

    result = await session.execute(
        select(Document).where(Document.source == source, Document.external_id == external_id)
    )
    document = result.scalar_one_or_none()

    if document is not None and document.content_hash == content_hash:
        existing = await session.execute(select(Chunk.id).where(Chunk.document_id == document.id).limit(1))
        if existing.first() is not None:
            logger.info("Content unchanged for %s:%s, skipping re-embed", source, external_id)
            document.synced_at = datetime.now(timezone.utc)
            await session.commit()
            return

    if document is None:
        document = Document(
            id=uuid.uuid4(),
            source=source,
            external_id=external_id,
            connection_id=connection_id,
            title=title,
            url=url,
        )
        session.add(document)

    document.title = title
    document.url = url
    document.content_hash = content_hash
    document.last_edited_at = _parse_datetime(last_edited_at)
    document.synced_at = datetime.now(timezone.utc)
    await session.flush()

    chunk_records: list[dict] = []
    for text, extra_metadata in sections:
        if not text:
            continue
        for chunk in chunk_text(text):
            metadata = dict(extra_metadata)
            if chunk["heading_path"]:
                metadata["heading_path"] = chunk["heading_path"]
            chunk_records.append({"content": chunk["content"], "metadata": metadata})

    await session.execute(delete(Chunk).where(Chunk.document_id == document.id))

    if chunk_records:
        embeddings = await embed_texts([c["content"] for c in chunk_records], input_type="document")
        for idx, (chunk, embedding) in enumerate(zip(chunk_records, embeddings)):
            session.add(
                Chunk(
                    id=uuid.uuid4(),
                    document_id=document.id,
                    chunk_index=idx,
                    content=chunk["content"],
                    embedding=embedding,
                    token_count=int(len(chunk["content"].split()) * 1.3),
                    metadata_=chunk["metadata"],
                )
            )

    await session.commit()
    logger.info("Embedded %d chunks for %s:%s", len(chunk_records), source, external_id)


async def handle_notion_page_updated(payload: dict) -> None:
    page_id = payload["page_id"]
    connection_id = payload["connection_id"]

    with tracer.start_as_current_span("embed.notion_page", attributes={"page_id": page_id}):
        async with get_session() as session:
            connection = await session.get(OAuthConnection, uuid.UUID(connection_id))
            if connection is None:
                logger.warning("No oauth_connections row for connection_id=%s, dropping", connection_id)
                return

            client = NotionClient(connection.access_token)
            metadata = await client.fetch_page_metadata(page_id)
            content = await client.fetch_page_content(page_id)

            full_text = f"{metadata['title']}\n\n{content}" if content else metadata["title"]

            await _sync_document(
                session,
                source="notion",
                external_id=page_id,
                connection_id=connection.id,
                title=metadata["title"],
                url=metadata["url"],
                last_edited_at=metadata.get("last_edited_time"),
                sections=[(full_text, {})],
            )


async def handle_jira_issue_updated(payload: dict) -> None:
    issue_key = payload["issue_key"]
    connection_id = payload["connection_id"]

    with tracer.start_as_current_span("embed.jira_issue", attributes={"issue_key": issue_key}):
        async with get_session() as session:
            connection = await session.get(OAuthConnection, uuid.UUID(connection_id))
            if connection is None:
                logger.warning("No oauth_connections row for connection_id=%s, dropping", connection_id)
                return

            client = JiraClient(connection)
            issue = await client.fetch_issue(issue_key)

            main_text = f"{issue['title']}\n\n{issue['description']}".strip()
            sections: list[tuple[str, dict]] = [(main_text, {})]
            # each comment chunked independently so a long thread doesn't
            # dilute the main ticket description's context
            for comment in issue["comments"]:
                sections.append((comment, {"jira_field": "comment"}))

            await _sync_document(
                session,
                source="jira",
                external_id=payload.get("issue_id", issue_key),
                connection_id=connection.id,
                title=issue["title"],
                url=issue["url"],
                last_edited_at=issue.get("updated"),
                sections=sections,
            )

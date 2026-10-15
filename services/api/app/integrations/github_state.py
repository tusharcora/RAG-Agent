"""GitHub sync state that must be cleared whenever a connection's documents are deleted."""

import uuid

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rag import GithubFailure, GithubRepo


async def reset_github_state(session: AsyncSession, connection_id: uuid.UUID) -> None:
    """Drop the connection's per-repo cursors and recorded failures so the next
    sync starts from scratch instead of seeing "nothing changed" for documents
    that no longer exist. Does not commit; the caller commits with its own change."""
    await session.execute(delete(GithubRepo).where(GithubRepo.connection_id == connection_id))
    await session.execute(delete(GithubFailure).where(GithubFailure.connection_id == connection_id))

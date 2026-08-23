import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import update

from app.core.db import get_session
from app.models.event_log import EventLog

logger = logging.getLogger(__name__)


async def _set_status(event_log_id: str, status: str, error: str | None = None) -> None:
    try:
        event_id = uuid.UUID(event_log_id)
    except ValueError:
        logger.warning("Malformed event_log_id header: %s", event_log_id)
        return

    async with get_session() as session:
        values = {"status": status, "updated_at": datetime.now(timezone.utc)}
        if error is not None:
            values["error"] = error
        await session.execute(update(EventLog).where(EventLog.id == event_id).values(**values))
        await session.commit()


async def mark_processing(event_log_id: str) -> None:
    await _set_status(event_log_id, "processing")


async def mark_succeeded(event_log_id: str) -> None:
    await _set_status(event_log_id, "succeeded")


async def mark_failed(event_log_id: str, error: str) -> None:
    await _set_status(event_log_id, "failed", error)


async def mark_dead_lettered(event_log_id: str, error: str) -> None:
    await _set_status(event_log_id, "dead_lettered", error)

import json
import logging
import uuid
from typing import Any

import aio_pika
from aio_pika import ExchangeType, Message
from aio_pika.abc import AbstractRobustConnection
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.telemetry import current_trace_id_hex
from app.models.event_log import EventLog

logger = logging.getLogger(__name__)

EVENTS_EXCHANGE = "backbone.events"
DLQ_EXCHANGE = "backbone.events.dlq"

_connection: AbstractRobustConnection | None = None


@retry(
    reraise=True,
    stop=stop_after_attempt(10),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    before_sleep=lambda retry_state: logger.warning(
        "RabbitMQ not ready yet (attempt %s), retrying: %s", retry_state.attempt_number, retry_state.outcome.exception()
    ),
)
async def _connect_robust() -> AbstractRobustConnection:
    return await aio_pika.connect_robust(settings.rabbitmq_url)


async def get_queue_connection() -> AbstractRobustConnection:
    """`depends_on: condition: service_healthy` in docker-compose.yml usually
    means RabbitMQ is ready before this ever runs, but its healthcheck can
    pass a moment before the AMQP listener actually accepts connections — a
    narrow but real race, especially on a cold `docker compose up`. Retrying
    the initial connect (not just relying on connect_robust's *post-connect*
    reconnection logic, which doesn't cover this first-attempt failure) means
    a losing race here degrades to a slow-but-successful startup rather than
    a crashed app that requires a manual restart to recover from.
    """
    global _connection
    if _connection is None or _connection.is_closed:
        _connection = await _connect_robust()
        channel = await _connection.channel()

        # Main topic exchange: routing_key = "<project>.<event_type>", e.g. "rag.notion_page_updated"
        # or "codereview.pull_request_opened"
        await channel.declare_exchange(EVENTS_EXCHANGE, ExchangeType.TOPIC, durable=True)

        # Dead-letter exchange: failed/rejected messages land here for inspection & manual retry
        await channel.declare_exchange(DLQ_EXCHANGE, ExchangeType.TOPIC, durable=True)

        logger.info("RabbitMQ connection + exchanges ready")
    return _connection


async def close_queue_connection() -> None:
    global _connection
    if _connection is not None:
        await _connection.close()
        _connection = None


async def publish_event(
    session: AsyncSession,
    routing_key: str,
    payload: dict[str, Any],
    dedupe_key: str,
    org_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Publishes an event onto the shared exchange and records it in
    `event_log` (status starts at "received"; the worker moves it through
    processing/succeeded/failed/dead_lettered via the `event_log_id` header).

    `dedupe_key` (e.g. a commit SHA + file path, or a Notion page ID + version)
    is carried as a message header so the worker can check it against Redis
    before processing, making delivery effectively idempotent even though
    GitHub/queue redelivery can send the same event twice.

    The event_log row is committed BEFORE publishing so it's durably visible
    to the worker before the message can possibly be consumed. If the publish
    itself then fails (broker down, network blip), the row is marked "failed"
    rather than left orphaned at "received" forever — otherwise a demo-time
    RabbitMQ hiccup would show up as a permanently-stuck row with no
    explanation in the Activity feed.
    """
    event = EventLog(
        id=uuid.uuid4(),
        routing_key=routing_key,
        dedupe_key=dedupe_key,
        payload=payload,
        status="received",
        trace_id=current_trace_id_hex(),
        org_id=org_id,
    )
    session.add(event)
    await session.commit()

    try:
        connection = await get_queue_connection()
        channel = await connection.channel()
        exchange = await channel.get_exchange(EVENTS_EXCHANGE)

        message = Message(
            body=json.dumps(payload).encode(),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            headers={"dedupe_key": dedupe_key, "event_log_id": str(event.id)},
        )
        await exchange.publish(message, routing_key=routing_key)
    except Exception as exc:
        logger.exception("Publish failed for event_log_id=%s routing_key=%s", event.id, routing_key)
        event.status = "failed"
        event.error = f"publish failed: {exc}"
        await session.commit()
        raise

    logger.info("Published event routing_key=%s dedupe_key=%s event_log_id=%s", routing_key, dedupe_key, event.id)
    return event.id

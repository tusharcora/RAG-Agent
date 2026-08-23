import asyncio
import json
import logging

import aio_pika
from aio_pika import ExchangeType, Message
from aio_pika.abc import AbstractIncomingMessage, AbstractExchange
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.telemetry import setup_telemetry, tracer
from app.core.idempotency import already_processed, clear_dedupe
from app.core.db import init_engine
from app.core.event_log import mark_dead_lettered, mark_failed, mark_processing, mark_succeeded
from app.tasks import dispatch

logger = logging.getLogger(__name__)

EVENTS_EXCHANGE = "backbone.events"
DLQ_EXCHANGE = "backbone.events.dlq"
QUEUE_NAME = "backbone.worker"

# Routing keys this worker cares about. "*.# " style topic patterns let one
# consumer handle both projects' events; split into separate queues/workers
# later if RAG and code-review workloads need independent scaling.
BINDING_KEYS = ["rag.#", "codereview.#"]

_events_exchange: AbstractExchange | None = None


async def _republish_with_retry(message: AbstractIncomingMessage, retry_count: int) -> None:
    """AMQP's native requeue (reject(requeue=True)) redelivers the message
    unchanged — there's no way to bump a header on it, so x-retry-count would
    never move off 0 and messages would requeue indefinitely instead of ever
    reaching the DLQ. Retrying is therefore done by acking the original and
    manually republishing a copy with x-retry-count incremented."""
    if _events_exchange is None:
        logger.error("No exchange reference to republish a retry — dropping instead of looping forever")
        return
    headers = dict(message.headers or {})
    headers["x-retry-count"] = retry_count
    new_message = Message(
        body=message.body,
        content_type=message.content_type,
        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        headers=headers,
    )
    await _events_exchange.publish(new_message, routing_key=message.routing_key or "")


async def handle_message(message: AbstractIncomingMessage) -> None:
    dedupe_key = message.headers.get("dedupe_key", "") if message.headers else ""
    routing_key = message.routing_key or ""
    # Optional: only present on messages published via app.core.queue.publish_event
    # (api service). Absent for anything published by hand — every call below
    # is a no-op in that case, so this stays purely additive instrumentation.
    event_log_id = (message.headers or {}).get("event_log_id")

    with tracer.start_as_current_span(
        "process_event",
        attributes={"routing_key": routing_key, "dedupe_key": dedupe_key},
    ):
        try:
            if dedupe_key and await already_processed(dedupe_key):
                logger.info("Skipping duplicate event dedupe_key=%s", dedupe_key)
                if event_log_id:
                    await mark_succeeded(event_log_id)
                await message.ack()
                return

            if event_log_id:
                await mark_processing(event_log_id)

            payload = json.loads(message.body)
            await dispatch(routing_key, payload)
            await message.ack()

            if event_log_id:
                await mark_succeeded(event_log_id)

        except Exception as exc:
            logger.exception("Failed processing routing_key=%s dedupe_key=%s", routing_key, dedupe_key)
            if event_log_id:
                await mark_failed(event_log_id, str(exc))
            if dedupe_key:
                # Processing didn't actually succeed — release the dedupe claim
                # so the retry below gets a real attempt instead of being
                # skipped as a false "duplicate" (which would silently report
                # this failure as "succeeded").
                await clear_dedupe(dedupe_key)

            retry_count = int((message.headers or {}).get("x-retry-count", 0))
            if retry_count >= settings.max_retries:
                # requeue=False + a configured DLX on the queue sends this to the DLQ
                await message.reject(requeue=False)
                logger.error("Moved to DLQ after %s retries: %s", retry_count, dedupe_key)
                if event_log_id:
                    await mark_dead_lettered(event_log_id, str(exc))
            else:
                next_retry_count = retry_count + 1
                backoff_seconds = min(2**retry_count, 30)
                logger.warning(
                    "Retrying in %ss (attempt %s/%s): %s", backoff_seconds, next_retry_count, settings.max_retries, dedupe_key
                )
                await message.ack()  # remove the original — a fresh copy is republished below
                await asyncio.sleep(backoff_seconds)
                await _republish_with_retry(message, next_retry_count)


@retry(
    reraise=True,
    stop=stop_after_attempt(10),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    before_sleep=lambda retry_state: logger.warning(
        "RabbitMQ not ready yet (attempt %s), retrying: %s", retry_state.attempt_number, retry_state.outcome.exception()
    ),
)
async def _connect_robust():
    return await aio_pika.connect_robust(settings.rabbitmq_url)


async def main() -> None:
    global _events_exchange

    setup_telemetry()
    await init_engine()
    # See services/api/app/core/queue.py's get_queue_connection() for why the
    # initial connect (not just connect_robust's post-connect reconnection
    # logic) needs its own retry — docker-compose's service_healthy dependency
    # doesn't fully close the race against RabbitMQ's AMQP listener coming up.
    connection = await _connect_robust()
    channel = await connection.channel()
    await channel.set_qos(prefetch_count=settings.prefetch_count)

    events_exchange = await channel.declare_exchange(EVENTS_EXCHANGE, ExchangeType.TOPIC, durable=True)
    dlq_exchange = await channel.declare_exchange(DLQ_EXCHANGE, ExchangeType.TOPIC, durable=True)
    _events_exchange = events_exchange

    # Dead-letter queue: rejected messages land here for inspection/replay
    dlq = await channel.declare_queue("backbone.worker.dlq", durable=True)
    await dlq.bind(dlq_exchange, routing_key="#")

    main_queue = await channel.declare_queue(
        QUEUE_NAME,
        durable=True,
        arguments={
            "x-dead-letter-exchange": DLQ_EXCHANGE,
        },
    )
    for key in BINDING_KEYS:
        await main_queue.bind(events_exchange, routing_key=key)

    logger.info("Worker listening on '%s' for routing keys %s", QUEUE_NAME, BINDING_KEYS)
    await main_queue.consume(handle_message)

    await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.tasks import github as github_tasks
from app.tasks.embed import handle_jira_issue_updated, handle_notion_page_updated

logger = logging.getLogger(__name__)

# Code review bot handlers get added here the same way, e.g.:
#   from app.tasks.analyze_diff import handle_pull_request_opened
#   HANDLERS["codereview.pull_request_opened"] = handle_pull_request_opened
#   HANDLERS["codereview.pull_request_synchronize"] = handle_pull_request_opened

HANDLERS: dict[str, Any] = {
    "rag.notion_page_updated": handle_notion_page_updated,
    "rag.jira_issue_updated": handle_jira_issue_updated,
    **github_tasks.HANDLERS,
}

# Called with (payload, error) once an event is dead-lettered (main.py's RabbitMQ consumer and
# local_queue.py's desktop queue, including a job the worker died during past max_retries).
ON_DEAD_LETTER: dict[str, Callable[[dict, str], Awaitable[None]]] = {
    **github_tasks.ON_DEAD_LETTER,
}


async def dispatch(routing_key: str, payload: dict[str, Any]) -> None:
    handler = HANDLERS.get(routing_key)
    if handler is None:
        logger.warning("No handler registered for routing_key=%s, dropping (acked)", routing_key)
        return
    await handler(payload)


async def on_dead_letter(routing_key: str, payload: Any, error: str) -> None:
    """Runs the routing key's dead-letter hook, if any. Never raises: a failing hook is logged and
    the event stays dead-lettered exactly as it would without one."""
    hook = ON_DEAD_LETTER.get(routing_key)
    if hook is None or not isinstance(payload, dict):
        return
    try:
        await hook(payload, error)
    except Exception:
        logger.exception("Dead-letter hook for routing_key=%s failed (event stays dead-lettered)", routing_key)

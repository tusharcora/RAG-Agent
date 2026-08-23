import logging
from typing import Any

from app.tasks.embed import handle_jira_issue_updated, handle_notion_page_updated

logger = logging.getLogger(__name__)

# Code review bot handlers get added here the same way, e.g.:
#   from app.tasks.analyze_diff import handle_pull_request_opened
#   HANDLERS["codereview.pull_request_opened"] = handle_pull_request_opened
#   HANDLERS["codereview.pull_request_synchronize"] = handle_pull_request_opened

HANDLERS: dict[str, Any] = {
    "rag.notion_page_updated": handle_notion_page_updated,
    "rag.jira_issue_updated": handle_jira_issue_updated,
}


async def dispatch(routing_key: str, payload: dict[str, Any]) -> None:
    handler = HANDLERS.get(routing_key)
    if handler is None:
        logger.warning("No handler registered for routing_key=%s, dropping (acked)", routing_key)
        return
    await handler(payload)

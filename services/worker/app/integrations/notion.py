import logging

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import settings

logger = logging.getLogger(__name__)

NOTION_API_BASE = "https://api.notion.com/v1"

_HEADING_TYPES = {"heading_1": 1, "heading_2": 2, "heading_3": 3}


class NotionRateLimited(Exception):
    def __init__(self, retry_after: float):
        self.retry_after = retry_after
        super().__init__(f"Notion rate limited, retry after {retry_after}s")


def _notion_wait(retry_state):
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if isinstance(exc, NotionRateLimited):
        return exc.retry_after
    return wait_exponential(multiplier=1, min=1, max=30)(retry_state)


@retry(retry=retry_if_exception_type(NotionRateLimited), wait=_notion_wait, stop=stop_after_attempt(5), reraise=True)
async def _request(client: httpx.AsyncClient, method: str, url: str, **kwargs) -> httpx.Response:
    """Notion allows ~3 req/s per integration; on any workspace of real size
    the recursive block-children fetch below bursts past that, so every call
    goes through this retry/backoff wrapper, honoring Retry-After when present."""
    resp = await client.request(method, url, **kwargs)
    if resp.status_code == 429:
        raise NotionRateLimited(retry_after=float(resp.headers.get("Retry-After", 1)))
    resp.raise_for_status()
    return resp


def _extract_title(page_data: dict) -> str:
    for prop in page_data.get("properties", {}).values():
        if prop.get("type") == "title":
            parts = prop.get("title", [])
            title = "".join(t.get("plain_text", "") for t in parts)
            return title or "Untitled"
    return "Untitled"


def _blocks_to_text(blocks: list[dict]) -> str:
    lines: list[str] = []
    for block in blocks:
        block_type = block.get("type")
        rich_text = block.get(block_type, {}).get("rich_text", [])
        text = "".join(t.get("plain_text", "") for t in rich_text)
        if block_type in _HEADING_TYPES:
            lines.append(f"{'#' * _HEADING_TYPES[block_type]} {text}")
        elif text:
            lines.append(text)
        if block.get("_children"):
            child_text = _blocks_to_text(block["_children"])
            if child_text:
                lines.append(child_text)
    return "\n\n".join(l for l in lines if l)


class NotionClient:
    def __init__(self, access_token: str):
        self._headers = {
            "Authorization": f"Bearer {access_token}",
            "Notion-Version": settings.notion_api_version,
            "Content-Type": "application/json",
        }

    async def fetch_page_metadata(self, page_id: str) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await _request(client, "GET", f"{NOTION_API_BASE}/pages/{page_id}", headers=self._headers)
        data = resp.json()
        return {
            "title": _extract_title(data),
            "url": data.get("url", f"https://notion.so/{page_id.replace('-', '')}"),
            "last_edited_time": data.get("last_edited_time"),
        }

    async def fetch_page_content(self, page_id: str) -> str:
        async with httpx.AsyncClient(timeout=30) as client:
            blocks = await self._fetch_blocks(client, page_id)
        return _blocks_to_text(blocks)

    async def _fetch_blocks(self, client: httpx.AsyncClient, block_id: str) -> list[dict]:
        blocks: list[dict] = []
        cursor: str | None = None
        while True:
            params = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            resp = await _request(
                client, "GET", f"{NOTION_API_BASE}/blocks/{block_id}/children", headers=self._headers, params=params
            )
            data = resp.json()
            for block in data.get("results", []):
                block["_children"] = (
                    await self._fetch_blocks(client, block["id"]) if block.get("has_children") else []
                )
                blocks.append(block)
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
        return blocks

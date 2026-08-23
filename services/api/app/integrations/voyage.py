import logging
from typing import Literal

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import settings

logger = logging.getLogger(__name__)

VOYAGE_API_URL = "https://api.voyageai.com/v1/embeddings"


class VoyageRateLimited(Exception):
    def __init__(self, retry_after: float):
        self.retry_after = retry_after
        super().__init__(f"Voyage rate limited, retry after {retry_after}s")


def _voyage_wait(retry_state):
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if isinstance(exc, VoyageRateLimited):
        return exc.retry_after
    return wait_exponential(multiplier=1, min=1, max=30)(retry_state)


@retry(retry=retry_if_exception_type(VoyageRateLimited), wait=_voyage_wait, stop=stop_after_attempt(5), reraise=True)
async def embed_texts(texts: list[str], input_type: Literal["document", "query"]) -> list[list[float]]:
    """Raw httpx against Voyage's REST API rather than the voyageai SDK —
    matches this repo's convention of no dedicated SDK wrappers except the
    officially-recommended `anthropic` package."""
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            VOYAGE_API_URL,
            headers={"Authorization": f"Bearer {settings.voyage_api_key}"},
            json={"input": texts, "model": settings.embedding_model, "input_type": input_type},
        )
        if resp.status_code == 429:
            raise VoyageRateLimited(retry_after=float(resp.headers.get("Retry-After", 1)))
        resp.raise_for_status()
        data = resp.json()
    return [item["embedding"] for item in data["data"]]

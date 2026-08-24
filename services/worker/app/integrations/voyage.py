import asyncio
import logging
import time
from typing import Literal

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import settings

logger = logging.getLogger(__name__)

VOYAGE_API_URL = "https://api.voyageai.com/v1/embeddings"

# Module-level so it's shared across every embed_texts() call in this worker
# process, not per-call — the whole point is bounding *total* concurrent
# requests to Voyage regardless of how many documents are being embedded at
# once. Kept as a secondary safety net alongside the rate gate below.
_voyage_semaphore = asyncio.Semaphore(settings.voyage_max_concurrent_requests)


class _RateGate:
    """Proactively paces calls to at most `rate_per_minute`, spaced evenly
    rather than let-them-through-then-punish. A concurrency semaphore alone
    doesn't prevent bursting past a per-minute quota — 3 tasks released by a
    semaphore of size 3 can still all fire within the same second. This
    tracks a single shared "next allowed dispatch time" and makes every
    caller wait for its turn before the request goes out at all, so the
    worker self-throttles to Voyage's real budget instead of finding out
    it's over budget via a 429."""

    def __init__(self, rate_per_minute: int):
        self._interval = 60.0 / rate_per_minute
        self._lock = asyncio.Lock()
        self._next_allowed = 0.0

    async def wait_turn(self) -> None:
        async with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next_allowed - now)
            self._next_allowed = max(now, self._next_allowed) + self._interval
        if delay:
            await asyncio.sleep(delay)


_voyage_rate_gate = _RateGate(settings.voyage_requests_per_minute)
# Floor for reactive backoff on an actual 429, in case the rate gate above
# and Voyage's own accounting still disagree (clock drift, another process
# sharing the same key). Voyage's Retry-After was observed reporting ~1s —
# far shorter than the ~20s a 3 RPM budget actually needs — so a retry must
# never trust it blindly; wait at least one full interval regardless of what
# the header says.
_MIN_RETRY_WAIT = 60.0 / settings.voyage_requests_per_minute


class VoyageRateLimited(Exception):
    def __init__(self, retry_after: float):
        self.retry_after = retry_after
        super().__init__(f"Voyage rate limited, retry after {retry_after}s")


def _voyage_wait(retry_state):
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if isinstance(exc, VoyageRateLimited):
        return max(exc.retry_after, _MIN_RETRY_WAIT)
    return wait_exponential(multiplier=1, min=1, max=30)(retry_state)


@retry(retry=retry_if_exception_type(VoyageRateLimited), wait=_voyage_wait, stop=stop_after_attempt(5), reraise=True)
async def embed_texts(texts: list[str], input_type: Literal["document", "query"]) -> list[list[float]]:
    """Raw httpx against Voyage's REST API rather than the voyageai SDK —
    matches this repo's convention of no dedicated SDK wrappers except the
    officially-recommended `anthropic` package."""
    # Held only around the actual in-flight request, not the tenacity backoff
    # sleep on a 429 — releasing it during backoff lets other pending
    # embed_texts() calls take their turn instead of all queuing up behind
    # whichever call happened to hit the rate limit first.
    async with _voyage_semaphore:
        await _voyage_rate_gate.wait_turn()
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

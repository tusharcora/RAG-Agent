import asyncio
import logging
import time
from typing import Literal

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.redis import get_redis

logger = logging.getLogger(__name__)

VOYAGE_API_URL = "https://api.voyageai.com/v1/embeddings"
# Shared with services/worker's copy of this module — both hit the same
# Voyage account/key (this service embeds query text at /query time, worker
# embeds documents), so cumulative usage has to be tracked in one place both
# processes can see, not per-process. No TTL: this is meant to persist for
# the life of the free-tier allotment, not expire like other keys this Redis
# client is used for elsewhere in this service (oauth_state, sessions).
VOYAGE_TOKENS_USED_KEY = "voyage:tokens_used_total"

# Module-level so it's shared across every embed_texts() call in this
# process, not per-call — bounds *total* concurrent requests to Voyage
# regardless of how many /query calls land at once. Kept as a secondary
# safety net alongside the rate gate below.
_voyage_semaphore = asyncio.Semaphore(settings.voyage_max_concurrent_requests)


class _RateGate:
    """Proactively paces calls to at most `rate_per_minute`, spaced evenly
    rather than let-them-through-then-punish. A concurrency semaphore alone
    doesn't prevent bursting past a per-minute quota — 3 tasks released by a
    semaphore of size 3 can still all fire within the same second. This
    tracks a single shared "next allowed dispatch time" and makes every
    caller wait for its turn before the request goes out at all, so this
    process self-throttles to Voyage's real budget instead of finding out
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
# and Voyage's own accounting still disagree (clock drift, the worker
# process sharing the same key). Voyage's Retry-After was observed reporting
# ~1s — far shorter than the ~20s a 3 RPM budget actually needs — so a retry
# must never trust it blindly; wait at least one full interval regardless of
# what the header says.
_MIN_RETRY_WAIT = 60.0 / settings.voyage_requests_per_minute


class VoyageRateLimited(Exception):
    def __init__(self, retry_after: float):
        self.retry_after = retry_after
        super().__init__(f"Voyage rate limited, retry after {retry_after}s")


class VoyageBudgetExceeded(Exception):
    """Raised instead of ever calling Voyage once cumulative usage has
    reached the configured free-tier budget. Deliberately NOT a
    VoyageRateLimited subtype — tenacity's retry below only matches
    VoyageRateLimited, so this propagates immediately instead of burning
    retries against a limit that waiting doesn't fix."""

    def __init__(self, used: int, budget: int):
        self.used = used
        self.budget = budget
        super().__init__(f"Voyage free-tier token budget reached: {used}/{budget} tokens used")


def _voyage_wait(retry_state):
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if isinstance(exc, VoyageRateLimited):
        return max(exc.retry_after, _MIN_RETRY_WAIT)
    return wait_exponential(multiplier=1, min=1, max=30)(retry_state)


async def _check_budget() -> None:
    """Checked before every call, not just logged after — this is meant to
    stop spend, not just report it. The per-call token cost isn't known
    until Voyage's response comes back, so this can't guarantee zero
    overshoot on the single call that happens to cross the line (bounded by
    voyage_max_concurrent_requests concurrent in-flight calls each up to one
    batch's worth of tokens), but it stops every call after that one — a
    best-effort cap, not exact-to-the-token."""
    used = int(await get_redis().get(VOYAGE_TOKENS_USED_KEY) or 0)
    budget = settings.voyage_free_tier_token_budget
    if used >= budget:
        raise VoyageBudgetExceeded(used, budget)


async def _record_usage(tokens: int) -> None:
    if tokens <= 0:
        return
    total = await get_redis().incrby(VOYAGE_TOKENS_USED_KEY, tokens)
    budget = settings.voyage_free_tier_token_budget
    if budget and total - tokens < budget * 0.9 <= total:
        logger.warning("voyage: cumulative usage %d/%d tokens — 90%%+ of free-tier budget", total, budget)


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
        await _check_budget()
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
    await _record_usage(data.get("usage", {}).get("total_tokens", 0))
    return [item["embedding"] for item in data["data"]]

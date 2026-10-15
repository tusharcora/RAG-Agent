"""Small GitHub REST/OAuth helpers for connecting the GitHub source (web OAuth
callback and the desktop finish page). REST calls go to `settings.github_api_url`;
the OAuth code exchange goes to github.com. Never log tokens or codes here."""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type

from app.core.config import settings

logger = logging.getLogger(__name__)

# Tests set this to an httpx.MockTransport.
TRANSPORT: httpx.AsyncBaseTransport | None = None

OAUTH_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
OAUTH_TOKEN_URL = "https://github.com/login/oauth/access_token"
NOT_GRANTED = "GitHub access wasn't granted."


class GitHubError(Exception):
    pass


class GitHubDenied(GitHubError):
    pass


class GitHubUnavailable(GitHubError):
    """Rate limited, 5xx or unreachable after retries, or a 401 even with a renewed token."""


class GitHubStatusError(GitHubError):
    """Any other non-2xx answer (404 for a repo removed meanwhile, ...)."""

    def __init__(self, status_code: int, path: str):
        super().__init__(f"GitHub answered {status_code} for {path}")
        self.status_code = status_code


def _client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=TRANSPORT, timeout=timeout)


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _api(path: str) -> str:
    return f"{settings.github_api_url.rstrip('/')}{path}"


async def exchange_code(code: str) -> dict:
    """The user-token response {access_token, refresh_token, expires_in, ...}.
    GitHub answers 200 with an `error` field when the code is bad -> GitHubDenied."""
    try:
        async with _client(30) as client:
            resp = await client.post(
                OAUTH_TOKEN_URL,
                data={
                    "client_id": settings.github_source_client_id,
                    "client_secret": settings.github_source_client_secret,
                    "code": code,
                    "redirect_uri": settings.github_source_redirect_uri,
                },
                headers={"Accept": "application/json"},
            )
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise GitHubError("Couldn't reach GitHub.") from exc
    if not isinstance(data, dict):
        raise GitHubError("Unexpected response from GitHub.")
    if data.get("error"):
        raise GitHubDenied(NOT_GRANTED)
    if resp.status_code != 200 or not data.get("access_token"):
        raise GitHubError("GitHub didn't return an access token.")
    return data


async def get_user(token: str) -> dict:
    """{id, login, ...} of the token's user."""
    try:
        async with _client(30) as client:
            resp = await client.get(_api("/user"), headers=_headers(token))
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise GitHubError("Couldn't read the GitHub account.") from exc
    if not isinstance(data, dict) or "id" not in data or not data.get("login"):
        raise GitHubError("Unexpected response from GitHub.")
    return data


async def count_repositories(token: str, installations: list[dict]) -> int | None:
    """Repositories the user's token can reach across `installations`; None on any
    failure (best effort, only used for the finish-page subtitle)."""
    try:
        total = 0
        async with _client(10) as client:
            for installation in installations:
                resp = await client.get(
                    _api(f"/user/installations/{int(installation['id'])}/repositories"),
                    params={"per_page": 1},
                    headers=_headers(token),
                )
                resp.raise_for_status()
                total += int(resp.json()["total_count"])
        return total
    except Exception as exc:  # noqa: BLE001 - never fail the finish page over a subtitle
        logger.info("GitHub repository count unavailable: %s", type(exc).__name__)
        return None


# --------------------------------------------------------------------------- sync client

# Tests replace this with a no-op.
_sleep = asyncio.sleep
RETRY_ATTEMPTS = 5
# A wait longer than this (an exhausted hourly quota) isn't slept through: the sync stops early and resumes next run.
MAX_WAIT_SECONDS = 60.0


class _Retry(Exception):
    def __init__(self, wait: float):
        self.wait = wait


def _rate_limit_message(resp: httpx.Response) -> bool:
    """A 403 whose message says it is a (secondary) rate limit, which GitHub may send without
    Retry-After ("You have exceeded a secondary rate limit", "API rate limit exceeded")."""
    try:
        return "rate limit" in resp.text.lower()
    except (UnicodeDecodeError, httpx.ResponseNotRead):
        return False


def _retry_wait(resp: httpx.Response, attempt: int) -> float | None:
    """Seconds to wait before retrying `resp`, or None when it isn't retryable: 5xx, 429 and the
    rate-limit 403s (secondary limits usually carry Retry-After; an exhausted quota
    x-ratelimit-remaining: 0; either may come with only a "rate limit" message, then backing off)."""
    status = resp.status_code
    retry_after = resp.headers.get("Retry-After")
    exhausted = resp.headers.get("x-ratelimit-remaining") == "0"
    limited_403 = status == 403 and (retry_after is not None or exhausted or _rate_limit_message(resp))
    if not (status >= 500 or status == 429 or limited_403):
        return None
    try:
        if retry_after is not None:
            return max(0.0, float(retry_after))
        if exhausted and resp.headers.get("x-ratelimit-reset"):
            return max(0.0, float(resp.headers["x-ratelimit-reset"]) - time.time())
    except ValueError:
        pass
    return float(min(2**attempt, 30))


def _wait(state) -> float:
    exc = state.outcome.exception() if state.outcome else None
    return exc.wait if isinstance(exc, _Retry) else float(min(2**state.attempt_number, 30))


def _stop(state) -> bool:
    exc = state.outcome.exception() if state.outcome else None
    return state.attempt_number >= RETRY_ATTEMPTS or (isinstance(exc, _Retry) and exc.wait > MAX_WAIT_SECONDS)


class GitHubClient:
    """GET-only REST client for sync enumeration: retries rate limits, 5xx and network errors
    (honouring Retry-After / x-ratelimit-reset), and on a 401 asks `refresh(failed_token)` for a
    new token once (github_auth's locked refresh) and repeats the call. Use as `async with`."""

    def __init__(self, token: str, refresh: Callable[[str], Awaitable[str]]):
        self.token = token
        self._refresh = refresh
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "GitHubClient":
        self._http = _client(30)
        return self

    async def __aexit__(self, *exc) -> None:
        await self._http.aclose()

    async def _send(self, url: str, params: dict | None) -> httpx.Response:
        try:
            async for attempt in AsyncRetrying(
                retry=retry_if_exception_type((_Retry, httpx.TransportError)),
                wait=_wait,
                stop=_stop,
                sleep=lambda seconds: _sleep(seconds),
                reraise=True,
            ):
                with attempt:
                    resp = await self._http.get(url, params=params, headers=_headers(self.token))
                    wait = _retry_wait(resp, attempt.retry_state.attempt_number)
                    if wait is not None:
                        raise _Retry(wait)
            return resp
        except (_Retry, httpx.TransportError) as exc:
            raise GitHubUnavailable(f"GitHub unavailable for {httpx.URL(url).path}") from exc

    async def get(self, path_or_url: str, params: dict | None = None) -> httpx.Response:
        """The response for any status except 401/retryable ones; callers check the status."""
        url = path_or_url if path_or_url.startswith("http") else _api(path_or_url)
        resp = await self._send(url, params)
        if resp.status_code == 401:
            self.token = await self._refresh(self.token)
            resp = await self._send(url, params)
            if resp.status_code == 401:
                raise GitHubUnavailable("GitHub refused the renewed token.")
        return resp

    async def pages(self, path: str, params: dict | None = None, key: str | None = None) -> AsyncIterator[list]:
        """Each page of a paginated listing (following Link rel="next"); `key` picks the list out of
        an object body such as {"total_count", "repositories": [...]}. Raises on any non-200 page."""
        url: str | None = path
        params = {"per_page": 100, **(params or {})}
        while url:
            resp = await self.get(url, params)
            if resp.status_code != 200:
                raise GitHubStatusError(resp.status_code, httpx.URL(resp.request.url).path)
            body = resp.json()
            yield body.get(key, []) if key else body
            url = resp.links.get("next", {}).get("url")
            params = None  # the next link carries them

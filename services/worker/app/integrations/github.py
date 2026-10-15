"""GitHub for the worker: the GitHub App user-token refresh, and `GithubClient`, the REST
client the handlers in app/tasks/github.py fetch files/issues/PRs with.

The worker's copy of the api's app/integrations/github_auth.py (duplicated per this
repo's api/worker convention), raising module exceptions instead of HTTPException.
Spec §4: the refresh runs in its own short session, never a handler's, with the
connection row locked (SELECT … FOR UPDATE) so concurrent handlers and the api's sync
refresh at most once — the others wait on the lock, re-read the row and reuse the
token the first wrote. GitHub rotates the refresh token on every use.

Never log tokens or refresh tokens from this module.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

import httpx
from cryptography.fernet import InvalidToken
from tenacity import AsyncRetrying, retry_if_exception_type
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from app.core.config import settings
from app.core.crypto import decrypt_token, encrypt_token
from app.core.db import get_session
from app.integrations import broker
from app.models.rag import OAuthConnection

logger = logging.getLogger(__name__)

GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
RECONNECT = "GitHub needs reconnecting. Use Reconnect on the Connections page."
DISCONNECTED = "GitHub is disconnected. Connect it again to sync."
UNAVAILABLE = "Couldn't reach GitHub to renew access. Try again in a moment."
API_UNAVAILABLE = "GitHub is rate limiting or unavailable right now. Try again in a moment."
TOKEN_REFUSED = "GitHub refused the renewed access token. Try again in a moment."
REFRESH_MARGIN = timedelta(minutes=5)
# How long to wait for the connection row's lock. Longer than the slowest refresh another holder can be doing
# (30 s HTTP timeout), so a concurrent refresher is waited for; a holder that never commits ends as "unavailable"
# instead of hanging. Module constant so tests can shorten it.
LOCK_TIMEOUT = "45s"
DEFAULT_EXPIRES_IN = 28800  # GitHub App user tokens last 8 hours


class GithubNeedsReconnect(Exception):
    """GitHub (or the sign-in Worker) refused the refresh token; needs_reconnect_at is set."""

    def __init__(self, message: str = RECONNECT):
        super().__init__(message)


class GithubUnavailable(Exception):
    """Transient: GitHub or the sign-in Worker couldn't be reached. No flag is set."""

    def __init__(self, message: str = UNAVAILABLE):
        super().__init__(message)


class GithubDisconnected(Exception):
    """The connection is disconnected (tokens wiped) or gone."""

    def __init__(self, message: str = DISCONNECTED):
        super().__init__(message)


class _Refused(Exception):
    pass


async def _refresh(refresh_token: str) -> dict:
    """{access_token, refresh_token?, expires_in?} or _Refused / GithubUnavailable."""
    if settings.app_mode == "desktop":
        # The desktop app has no client secret; the sign-in Worker holds it.
        try:
            return await broker.refresh_github_repos(refresh_token)
        except broker.BrokerRefused:
            raise _Refused()
        except broker.BrokerUnavailable as exc:
            raise GithubUnavailable(str(exc))

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                GITHUB_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "client_id": settings.github_source_client_id,
                    "client_secret": settings.github_source_client_secret,
                    "refresh_token": refresh_token,
                },
                headers={"Accept": "application/json"},
            )
    except httpx.HTTPError:
        raise GithubUnavailable()
    if resp.status_code != 200:
        raise GithubUnavailable()
    try:
        data = resp.json()
    except ValueError:
        raise GithubUnavailable()
    if not isinstance(data, dict):
        raise GithubUnavailable()
    # A failed refresh is a 200 with an `error` field; only bad_refresh_token means reconnect.
    if data.get("error"):
        if data["error"] == "bad_refresh_token":
            raise _Refused()
        raise GithubUnavailable()
    if not data.get("access_token"):
        raise GithubUnavailable()
    return data


def _is_lock_timeout(exc: DBAPIError) -> bool:
    """Postgres lock_not_available (55P03), however the driver error is wrapped."""
    for err in (exc.orig, getattr(exc.orig, "__cause__", None)):
        if getattr(err, "sqlstate", None) == "55P03" or getattr(err, "pgcode", None) == "55P03":
            return True
    return False


async def github_access_token(connection_id: uuid.UUID, failed_token: str | None = None) -> str:
    """A usable GitHub access token for the connection, refreshing it when it has under
    5 minutes left or when `failed_token` (the token a call just got a 401 with) is still
    the stored one. Raises GithubDisconnected / GithubNeedsReconnect / GithubUnavailable;
    undecryptable stored credentials raise InvalidToken (handler failure → retry → dead-letter).

    Opens its own session and locks the row. The caller must not hold uncommitted changes or
    locks on this connection's row — commit first — or this waits LOCK_TIMEOUT and then fails
    as unavailable."""
    async with get_session() as session:
        try:
            # SET LOCAL lasts for this transaction only (the pooled connection is not left changed).
            await session.execute(text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'"))
            connection = (
                await session.execute(
                    select(OAuthConnection)
                    .where(OAuthConnection.id == connection_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()
        except DBAPIError as exc:
            if not _is_lock_timeout(exc):
                raise
            logger.warning("Timed out waiting for the lock on connection %s", connection_id)
            await session.rollback()
            raise GithubUnavailable() from exc
        if connection is None or connection.disconnected_at is not None or not connection.access_token:
            raise GithubDisconnected()
        try:
            access_token = decrypt_token(connection.access_token)
            refresh_token = decrypt_token(connection.refresh_token) if connection.refresh_token else None
        except InvalidToken:
            logger.error(
                "connection_id=%s has undecryptable stored credentials — reconnect required", connection_id
            )
            raise

        now = datetime.now(timezone.utc)
        still_valid = connection.expires_at is None or connection.expires_at > now + REFRESH_MARGIN
        # Reuse unless expiring, or it is exactly the token that just got a 401 (revoked early).
        if still_valid and (failed_token is None or access_token != failed_token):
            return access_token

        logger.info("Refreshing GitHub access token for connection %s", connection_id)
        try:
            if refresh_token is None:
                raise _Refused()
            data = await _refresh(refresh_token)
        except _Refused:
            connection.needs_reconnect_at = now  # still locked and connected (checked above)
            await session.commit()
            raise GithubNeedsReconnect()
        except GithubUnavailable:
            await session.rollback()
            raise

        new_access_token = data["access_token"]
        values = {
            "access_token": encrypt_token(new_access_token),
            "refresh_token": encrypt_token(data.get("refresh_token") or refresh_token),
            "expires_at": now + timedelta(seconds=int(data.get("expires_in") or DEFAULT_EXPIRES_IN)),
            "needs_reconnect_at": None,
            "updated_at": now,
        }
        # Lock held since the read, so a disconnect is waiting rather than done; the condition keeps
        # tokens off a wiped row regardless.
        await session.execute(
            update(OAuthConnection)
            .where(OAuthConnection.id == connection_id, OAuthConnection.disconnected_at.is_(None))
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        await session.commit()
        return new_access_token


# ---------------------------------------------------------------- REST client

# Tests set this to an httpx.MockTransport.
TRANSPORT: httpx.AsyncBaseTransport | None = None
_sleep = asyncio.sleep
RETRY_ATTEMPTS = 5
# A wait longer than this (an exhausted hourly quota) isn't slept through in a handler: it fails as
# unavailable and the event's own retry/backoff takes over.
MAX_WAIT_SECONDS = 60.0
MAX_PAGES = 20
REDIRECTS = (301, 302, 307, 308)
MAX_REDIRECTS = 2


class GithubStatusError(Exception):
    """A non-2xx answer that isn't a rate limit, 5xx or 401 (callers decide what 404/410 mean)."""

    def __init__(self, status_code: int, path: str):
        super().__init__(f"GitHub answered {status_code} for {path}")
        self.status_code = status_code


class GithubContentError(Exception):
    """The item's own content couldn't be decoded or parsed (bad base64, unexpected JSON shape)."""


class _Retry(Exception):
    def __init__(self, wait: float):
        self.wait = wait


def _retry_wait(resp: httpx.Response, attempt: int) -> float | None:
    """Seconds to wait before retrying `resp`, or None when it isn't retryable: 5xx, 429 and the
    rate-limit 403s (secondary limits carry Retry-After; an exhausted quota x-ratelimit-remaining: 0)."""
    status = resp.status_code
    retry_after = resp.headers.get("Retry-After")
    exhausted = resp.headers.get("x-ratelimit-remaining") == "0"
    if not (status >= 500 or status == 429 or (status == 403 and (retry_after is not None or exhausted))):
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


class GithubClient:
    """GET-only REST client against `settings.github_api_url` for one connection and repository.

    Retries 5xx, 429, rate-limit 403s and network errors (honouring Retry-After / x-ratelimit-reset).
    The token comes from `github_access_token` (its own session and row lock — so a handler must
    not hold uncommitted writes on the connection row while using this client); a 401 asks it once
    for a renewed token (`failed_token`), then fails as unavailable. A redirect is followed only when
    it stays inside the same repository id (a renamed repository); any other redirect (an issue
    transferred elsewhere) is returned to the caller. Use as `async with`."""

    def __init__(self, connection_id: uuid.UUID, repo_id: int):
        self.connection_id = connection_id
        self.repo_id = int(repo_id)
        self.token: str | None = None
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "GithubClient":
        # Raises GithubDisconnected / GithubNeedsReconnect / GithubUnavailable before any API call.
        self.token = await github_access_token(self.connection_id)
        self._http = httpx.AsyncClient(transport=TRANSPORT, timeout=30)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._http is not None:
            await self._http.aclose()

    @staticmethod
    def _headers(token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

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
                    resp = await self._http.get(url, params=params, headers=self._headers(self.token))
                    wait = _retry_wait(resp, attempt.retry_state.attempt_number)
                    if wait is not None:
                        raise _Retry(wait)
            return resp
        except (_Retry, httpx.TransportError) as exc:
            logger.warning("GitHub unavailable for %s", httpx.URL(url).path)
            raise GithubUnavailable(API_UNAVAILABLE) from exc

    def _same_repo_redirect(self, resp: httpx.Response) -> str | None:
        location = resp.headers.get("Location")
        if resp.status_code not in REDIRECTS or not location:
            return None
        target = httpx.URL(resp.request.url).join(location)
        if target.path.startswith(f"/repositories/{self.repo_id}/"):
            return str(target)
        return None

    async def get(self, path_or_url: str, params: dict | None = None) -> httpx.Response:
        """The response for any status except 401/retryable ones; callers check the status."""
        url = path_or_url if path_or_url.startswith("http") else f"{settings.github_api_url.rstrip('/')}{path_or_url}"
        for _ in range(MAX_REDIRECTS + 1):
            resp = await self._send(url, params)
            if resp.status_code == 401:
                self.token = await github_access_token(self.connection_id, failed_token=self.token)
                resp = await self._send(url, params)
                if resp.status_code == 401:
                    raise GithubUnavailable(TOKEN_REFUSED)
            follow = self._same_repo_redirect(resp)
            if follow is None:
                return resp
            url, params = follow, None  # the Location carries any query
        # Still redirecting inside the same repository: not "gone", just not answerable.
        raise GithubStatusError(resp.status_code, httpx.URL(resp.request.url).path)

    async def pages(self, path: str, max_pages: int = MAX_PAGES) -> AsyncIterator[list]:
        """Each page of a listing (per_page=100, following Link rel="next"), at most `max_pages`.
        Raises GithubStatusError on any non-200 page."""
        url: str | None = path
        params: dict | None = {"per_page": 100}
        for _ in range(max_pages):
            if not url:
                return
            resp = await self.get(url, params)
            if resp.status_code != 200:
                raise GithubStatusError(resp.status_code, httpx.URL(resp.request.url).path)
            yield resp.json()
            url = resp.links.get("next", {}).get("url")
            params = None  # the next link carries them
        if url:
            logger.info("Stopped listing %s after %s pages", httpx.URL(path).path, max_pages)

    async def all(self, path: str) -> list:
        items: list = []
        async for page in self.pages(path):
            items.extend(page)
        return items

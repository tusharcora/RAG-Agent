"""GitHub for the worker. This part: the GitHub App user-token refresh (the client
that fetches files/issues/PRs builds on it).

The worker's copy of the api's app/integrations/github_auth.py (duplicated per this
repo's api/worker convention), raising module exceptions instead of HTTPException.
Spec §4: the refresh runs in its own short session, never a handler's, with the
connection row locked (SELECT … FOR UPDATE) so concurrent handlers and the api's sync
refresh at most once — the others wait on the lock, re-read the row and reuse the
token the first wrote. GitHub rotates the refresh token on every use.

Never log tokens or refresh tokens from this module.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from cryptography.fernet import InvalidToken
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

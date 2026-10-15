"""GitHub App user-token refresh for the api (sync enumeration calls GitHub directly).

The worker has an identical copy in app/integrations/github.py (duplicated per this
repo's api/worker convention). Spec §4: the refresh runs in its own short session,
never the caller's, with the connection row locked (SELECT … FOR UPDATE) so that
concurrent callers — a sync here, embed handlers in the worker — refresh at most once:
the second waits on the lock, re-reads the row and reuses the token the first wrote.
GitHub rotates the refresh token on every use, so two parallel refreshes with the same
refresh token would leave one of them holding a dead token.

Never log tokens or refresh tokens from this module.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from cryptography.fernet import InvalidToken
from fastapi import HTTPException
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from app.core import db
from app.core.config import settings
from app.core.crypto import decrypt_token, encrypt_token
from app.integrations import broker
from app.integrations.connection_state import require_connected
from app.models.rag import OAuthConnection

logger = logging.getLogger(__name__)

GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
RECONNECT = "GitHub needs reconnecting. Use Reconnect on the Connections page."
UNAVAILABLE = "Couldn't reach GitHub to renew access. Try again in a moment."
# A token with less than this left is refreshed before use.
REFRESH_MARGIN = timedelta(minutes=5)
# How long to wait for the connection row's lock. Longer than the slowest refresh another holder can be doing
# (30 s HTTP timeout), so a concurrent refresher is waited for; a holder that never commits ends as "unavailable"
# instead of hanging. Module constant so tests can shorten it.
LOCK_TIMEOUT = "45s"
DEFAULT_EXPIRES_IN = 28800  # GitHub App user tokens last 8 hours


class _Refused(Exception):
    pass


class _Unavailable(Exception):
    def __init__(self, detail: str = UNAVAILABLE):
        super().__init__(detail)
        self.detail = detail


async def _refresh(refresh_token: str) -> dict:
    """{access_token, refresh_token?, expires_in?} or _Refused / _Unavailable."""
    if settings.app_mode == "desktop":
        # The desktop app has no client secret; the sign-in Worker holds it.
        try:
            return await broker.refresh_github_repos(refresh_token)
        except broker.BrokerRefused:
            raise _Refused()
        except broker.BrokerUnavailable as exc:
            raise _Unavailable(str(exc))

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
        raise _Unavailable()
    if resp.status_code != 200:
        raise _Unavailable()
    try:
        data = resp.json()
    except ValueError:
        raise _Unavailable()
    if not isinstance(data, dict):
        raise _Unavailable()
    # GitHub answers a failed refresh with 200 and an `error` field. Only bad_refresh_token means the
    # person has to reconnect; anything else (e.g. misconfigured client credentials) is ours to fix.
    if data.get("error"):
        if data["error"] == "bad_refresh_token":
            raise _Refused()
        raise _Unavailable()
    if not data.get("access_token"):
        raise _Unavailable()
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
    the stored one. Raises HTTPException: 409 disconnected / needs reconnecting, 502
    GitHub or the sign-in Worker unreachable, 400 undecryptable stored credentials.

    Opens its own session and locks the row. The caller must not hold uncommitted changes or
    locks on this connection's row — commit first — or this waits LOCK_TIMEOUT and then fails
    as unavailable."""
    async with db._session_factory() as session:
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
            raise HTTPException(status_code=502, detail=UNAVAILABLE) from exc
        if connection is None:
            raise HTTPException(status_code=404, detail="Connection not found")
        require_connected(connection, "github")
        try:
            access_token = decrypt_token(connection.access_token)
            refresh_token = decrypt_token(connection.refresh_token) if connection.refresh_token else None
        except InvalidToken:
            raise HTTPException(
                status_code=400,
                detail="This connection's stored credentials are no longer valid — please reconnect",
            )

        now = datetime.now(timezone.utc)
        still_valid = connection.expires_at is None or connection.expires_at > now + REFRESH_MARGIN
        # Reuse unless it is expiring, or it is exactly the token that just got a 401 (revoked early). A token
        # another process already rotated in differs from failed_token, so it is reused without a refresh.
        if still_valid and (failed_token is None or access_token != failed_token):
            return access_token

        logger.info("Refreshing GitHub access token for connection %s", connection_id)
        try:
            if refresh_token is None:
                raise _Refused()
            data = await _refresh(refresh_token)
        except _Refused:
            # Still locked and connected (checked above), so the flag lands on a live row.
            connection.needs_reconnect_at = now
            await session.commit()
            raise HTTPException(status_code=409, detail=RECONNECT)
        except _Unavailable as exc:
            await session.rollback()
            raise HTTPException(status_code=502, detail=exc.detail)

        new_access_token = data["access_token"]
        values = {
            "access_token": encrypt_token(new_access_token),
            "refresh_token": encrypt_token(data.get("refresh_token") or refresh_token),
            "expires_at": now + timedelta(seconds=int(data.get("expires_in") or DEFAULT_EXPIRES_IN)),
            "needs_reconnect_at": None,
            "updated_at": now,
        }
        # The row lock is held since the read above, so a disconnect is waiting on it rather than done; the
        # condition keeps this write correct even if that ever changes (tokens never land on a wiped row).
        await session.execute(
            update(OAuthConnection)
            .where(OAuthConnection.id == connection_id, OAuthConnection.disconnected_at.is_(None))
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        await session.commit()
        return new_access_token

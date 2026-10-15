"""Client for the sign-in Worker (desktop/broker): Jira token refresh only.

The worker's copy of the refresh half of api/app/integrations/broker.py
(duplicated per this repo's api/worker convention — no shared package).
Never log refresh tokens or results from this module.
"""

import httpx

from app.core.config import settings

# Tests set this to an httpx.MockTransport.
TRANSPORT: httpx.AsyncBaseTransport | None = None

UNREACHABLE = "Couldn't reach the sign-in service. Check your internet connection."


class BrokerError(Exception):
    pass


class BrokerUnavailable(BrokerError):
    def __init__(self, message: str = UNREACHABLE):
        super().__init__(message)


class BrokerRefused(BrokerError):
    pass


def _base() -> str:
    return settings.auth_broker_url.rstrip("/")


def _client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=TRANSPORT, timeout=timeout)


def _json(resp: httpx.Response) -> dict:
    try:
        data = resp.json()
    except ValueError as exc:
        raise BrokerUnavailable() from exc
    if not isinstance(data, dict):
        raise BrokerUnavailable()
    return data


async def refresh_jira(refresh_token: str) -> dict:
    """{access_token, refresh_token, expires_in} from the Worker's /refresh/jira."""
    try:
        async with _client(15.0) as client:
            resp = await client.post(f"{_base()}/refresh/jira", json={"refresh_token": refresh_token})
    except httpx.HTTPError as exc:
        raise BrokerUnavailable() from exc
    if resp.status_code == 200:
        return _json(resp)
    # Only a 400 means Atlassian refused the refresh token (reconnect needed). Anything else — 404 (an old or
    # misconfigured Worker), 429, 5xx — is transient and must not tell the person to reconnect.
    if resp.status_code != 400:
        raise BrokerUnavailable()
    try:
        message = resp.json().get("error")
    except (ValueError, AttributeError):
        message = None
    raise BrokerRefused(str(message or "Jira refused the refresh. Reconnect Jira."))

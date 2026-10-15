"""Client for the sign-in Worker (desktop/broker) the desktop app uses for OAuth.

The Worker holds the provider client secrets; the app only ever sends it a
state id, a challenge, its loopback port, and later the verifier. Never log
verifiers, tokens or results from this module.
"""

from urllib.parse import quote, urlencode

import httpx

from app.core.config import settings

# Tests set this to an httpx.MockTransport.
TRANSPORT: httpx.AsyncBaseTransport | None = None

UNREACHABLE = "Couldn't reach the sign-in service. Check your internet connection."
EXPIRED = "This sign-in link has expired. Start again from the app."


class BrokerError(Exception):
    pass


class BrokerUnavailable(BrokerError):
    def __init__(self, message: str = UNREACHABLE):
        super().__init__(message)


class BrokerExpired(BrokerError):
    def __init__(self, message: str = EXPIRED):
        super().__init__(message)


class BrokerRefused(BrokerError):
    pass


def _base() -> str:
    return settings.auth_broker_url.rstrip("/")


def _client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=TRANSPORT, timeout=timeout)


def start_url(purpose: str, state: str, challenge: str, port: int) -> str:
    query = urlencode({"state": state, "challenge": challenge, "port": port})
    return f"{_base()}/start/{quote(purpose, safe='')}?{query}"


async def check_reachable() -> None:
    try:
        async with _client(2.0) as client:
            resp = await client.get(f"{_base()}/health")
    except httpx.HTTPError as exc:
        raise BrokerUnavailable() from exc
    if resp.status_code != 200:
        raise BrokerUnavailable()


def _json(resp: httpx.Response) -> dict:
    try:
        data = resp.json()
    except ValueError as exc:
        raise BrokerUnavailable() from exc
    if not isinstance(data, dict):
        raise BrokerUnavailable()
    return data


async def redeem(state: str, verifier: str) -> dict:
    """The sign-in result for `state`, released once by the Worker."""
    try:
        async with _client(15.0) as client:
            resp = await client.post(f"{_base()}/redeem", json={"state": state, "verifier": verifier})
    except httpx.HTTPError as exc:
        raise BrokerUnavailable() from exc
    if resp.status_code == 200:
        return _json(resp)
    if resp.status_code >= 500:
        raise BrokerUnavailable()
    # 403 (wrong verifier, entry burnt), 404 (unknown/expired), 400 (malformed).
    raise BrokerExpired()


async def refresh_jira(refresh_token: str) -> dict:
    """{access_token, refresh_token, expires_in} from the Worker's /refresh/jira."""
    try:
        async with _client(15.0) as client:
            resp = await client.post(f"{_base()}/refresh/jira", json={"refresh_token": refresh_token})
    except httpx.HTTPError as exc:
        raise BrokerUnavailable() from exc
    if resp.status_code == 200:
        return _json(resp)
    if resp.status_code >= 500:
        raise BrokerUnavailable()
    try:
        message = resp.json().get("error")
    except (ValueError, AttributeError):
        message = None
    raise BrokerRefused(str(message or "Jira refused the refresh. Reconnect Jira."))

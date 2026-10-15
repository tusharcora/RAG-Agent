"""Small GitHub REST/OAuth helpers for connecting the GitHub source (web OAuth
callback and the desktop finish page). REST calls go to `settings.github_api_url`;
the OAuth code exchange goes to github.com. Never log tokens or codes here."""

import logging

import httpx

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

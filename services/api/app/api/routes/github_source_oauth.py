"""Web (server mode) OAuth for connecting GitHub as a knowledge source, through
the GitHub App's user authorization. Same shape as jira_oauth.py: /authorize
stores the caller's org_id as the Redis CSRF state value, /callback reads it back.
Not the same thing as social_auth.py's "Continue with GitHub" sign-in.

Repositories are chosen on GitHub (the app's installation page); each sync lists
the user's installations, so none are stored here."""

import logging
import secrets
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthContext, require_auth, require_role
from app.core.config import settings
from app.core.db import get_session
from app.core.redis import get_redis
from app.integrations import github_api
from app.integrations.connections import save_github_connection

logger = logging.getLogger(__name__)


def _configured() -> None:
    if not settings.github_source_client_id:
        raise HTTPException(status_code=404, detail="Not found")


router = APIRouter(dependencies=[Depends(_configured)])


@router.get("/authorize")
async def authorize(auth: AuthContext = Depends(require_auth)) -> RedirectResponse:
    require_role(auth, "owner", "admin")
    state = secrets.token_urlsafe(32)
    await get_redis().set(f"oauth_state:github_source:{state}", str(auth.org_id), ex=settings.oauth_state_ttl_seconds)
    params = {
        "client_id": settings.github_source_client_id,
        "redirect_uri": settings.github_source_redirect_uri,
        "state": state,
    }
    return RedirectResponse(str(httpx.URL(github_api.OAUTH_AUTHORIZE_URL, params=params)))


@router.get("/callback")
async def callback(
    state: str = Query(...),
    code: str = Query(""),
    error: str = Query(""),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    redis = get_redis()
    state_key = f"oauth_state:github_source:{state}"
    org_id_raw = await redis.get(state_key)
    if not org_id_raw:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
    await redis.delete(state_key)
    org_id = uuid.UUID(org_id_raw)

    if error or not code:
        raise HTTPException(status_code=400, detail=github_api.NOT_GRANTED)
    try:
        token_data = await github_api.exchange_code(code)
        user = await github_api.get_user(token_data["access_token"])
    except github_api.GitHubDenied as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except github_api.GitHubError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    await save_github_connection(
        session,
        org_id,
        {
            "access_token": token_data["access_token"],
            "refresh_token": token_data.get("refresh_token"),
            "expires_in": token_data.get("expires_in"),
            "user_id": user["id"],
            "login": user["login"],
        },
    )
    return RedirectResponse(f"{settings.frontend_url}/connections?connected=github")

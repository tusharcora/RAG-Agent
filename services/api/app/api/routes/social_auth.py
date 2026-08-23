import json
import logging
import secrets
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.auth import _set_session_cookie, _slugify
from app.core.auth import create_access_token
from app.core.config import settings
from app.core.db import get_session
from app.core.redis import get_redis
from app.models.rag import OAuthIdentity, Organization, OrgInvite, OrgMember, User

logger = logging.getLogger(__name__)

# This is USER LOGIN into the app itself (an alternative to email+password
# via POST /auth/login), completely distinct from notion_oauth.py/jira_oauth.py
# which connect Notion/Jira as *data sources* for an org you're already
# logged into. Unlike those, /google/authorize and /github/authorize have no
# require_auth precondition — there's no session yet, that's the whole point.

google_router = APIRouter()
github_router = APIRouter()

GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"
GITHUB_EMAILS_URL = "https://api.github.com/user/emails"


async def _resolve_state(provider: str, state: str) -> dict:
    """Reads back the CSRF state payload set by /authorize (same
    Redis-CSRF-state pattern notion_oauth.py/jira_oauth.py use for org_id —
    here it optionally carries an invite_token instead, since there's no
    existing org to attribute to yet). 400s if expired/missing, same as a
    CSRF failure would."""
    redis = get_redis()
    state_key = f"oauth_state:{provider}:{state}"
    raw = await redis.get(state_key)
    if not raw:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
    await redis.delete(state_key)
    return json.loads(raw)


async def _store_state(provider: str, invite_token: str | None) -> str:
    state = secrets.token_urlsafe(32)
    await get_redis().set(
        f"oauth_state:{provider}:{state}",
        json.dumps({"invite_token": invite_token}),
        ex=settings.oauth_state_ttl_seconds,
    )
    return state


async def _login_via_identity(
    session: AsyncSession, provider: str, provider_user_id: str, email: str | None, display_name: str | None,
    invite_token: str | None,
) -> RedirectResponse:
    """Shared callback logic for both providers: find-or-link-or-create the
    user, resolve which org/role to issue a session for, then redirect to the
    frontend with the session cookie set — mirrors /auth/login's and
    /auth/signup's org-resolution behavior rather than inventing new rules."""
    identity_result = await session.execute(
        select(OAuthIdentity).where(
            OAuthIdentity.provider == provider, OAuthIdentity.provider_user_id == provider_user_id
        )
    )
    identity = identity_result.scalar_one_or_none()

    if identity is not None:
        user = await session.get(User, identity.user_id)
        if user is None:
            raise HTTPException(status_code=500, detail="Linked account no longer exists")
    else:
        user = None
        if email:
            existing_result = await session.execute(select(User).where(User.email == email))
            user = existing_result.scalar_one_or_none()

        is_new_user = user is None
        if user is None:
            if not email:
                raise HTTPException(
                    status_code=400, detail=f"{provider.title()} did not share an email address — cannot sign up"
                )
            user = User(id=uuid.uuid4(), email=email, password_hash=None, display_name=display_name)
            session.add(user)
            await session.flush()

        session.add(
            OAuthIdentity(
                id=uuid.uuid4(), provider=provider, provider_user_id=provider_user_id, user_id=user.id, email=email
            )
        )

        if is_new_user:
            # Same two paths /auth/signup offers: redeem a pending invite, or
            # create a brand-new org as owner.
            if invite_token:
                invite_result = await session.execute(
                    select(OrgInvite).where(OrgInvite.token == invite_token, OrgInvite.accepted_at.is_(None))
                )
                invite = invite_result.scalar_one_or_none()
                if invite is None or invite.expires_at < datetime.now(timezone.utc):
                    raise HTTPException(status_code=400, detail="Invalid or expired invite")
                if invite.email.lower() != user.email.lower():
                    raise HTTPException(status_code=400, detail="Invite was issued to a different email")
                invite.accepted_at = datetime.now(timezone.utc)
                session.add(OrgMember(id=uuid.uuid4(), org_id=invite.org_id, user_id=user.id, role=invite.role))
            else:
                org = Organization(id=uuid.uuid4(), name=display_name or email or "New org", slug=_slugify(display_name or email or "org"))
                session.add(org)
                await session.flush()
                session.add(OrgMember(id=uuid.uuid4(), org_id=org.id, user_id=user.id, role="owner"))

    membership_result = await session.execute(
        select(OrgMember).where(OrgMember.user_id == user.id).order_by(OrgMember.created_at)
    )
    memberships = membership_result.scalars().all()
    await session.commit()

    if not memberships:
        raise HTTPException(status_code=403, detail="Account has no organization")
    active = memberships[0]

    token = create_access_token(user.id, active.org_id, active.role)
    redirect = RedirectResponse(settings.frontend_url)
    _set_session_cookie(redirect, token)
    return redirect


@google_router.get("/authorize")
async def google_authorize(invite_token: str | None = Query(default=None)) -> RedirectResponse:
    if not settings.google_oauth_client_id:
        raise HTTPException(status_code=503, detail="Google login is not configured")
    state = await _store_state("google", invite_token)
    params = {
        "client_id": settings.google_oauth_client_id,
        "redirect_uri": settings.google_oauth_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    }
    return RedirectResponse(str(httpx.URL(GOOGLE_AUTHORIZE_URL, params=params)))


@google_router.get("/callback")
async def google_callback(
    code: str = Query(...), state: str = Query(...), session: AsyncSession = Depends(get_session)
) -> RedirectResponse:
    state_payload = await _resolve_state("google", state)

    async with httpx.AsyncClient(timeout=30) as client:
        token_resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": settings.google_oauth_client_id,
                "client_secret": settings.google_oauth_client_secret,
                "redirect_uri": settings.google_oauth_redirect_uri,
            },
        )
        token_resp.raise_for_status()
        access_token = token_resp.json()["access_token"]

        userinfo_resp = await client.get(GOOGLE_USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"})
        userinfo_resp.raise_for_status()
        userinfo = userinfo_resp.json()

    logger.info("Google login: sub=%s", userinfo.get("sub"))
    return await _login_via_identity(
        session,
        provider="google",
        provider_user_id=userinfo["sub"],
        email=userinfo.get("email"),
        display_name=userinfo.get("name"),
        invite_token=state_payload.get("invite_token"),
    )


@github_router.get("/authorize")
async def github_authorize(invite_token: str | None = Query(default=None)) -> RedirectResponse:
    if not settings.github_oauth_client_id:
        raise HTTPException(status_code=503, detail="GitHub login is not configured")
    state = await _store_state("github", invite_token)
    params = {
        "client_id": settings.github_oauth_client_id,
        "redirect_uri": settings.github_oauth_redirect_uri,
        "scope": "read:user user:email",
        "state": state,
    }
    return RedirectResponse(str(httpx.URL(GITHUB_AUTHORIZE_URL, params=params)))


@github_router.get("/callback")
async def github_callback(
    code: str = Query(...), state: str = Query(...), session: AsyncSession = Depends(get_session)
) -> RedirectResponse:
    state_payload = await _resolve_state("github", state)

    async with httpx.AsyncClient(timeout=30) as client:
        token_resp = await client.post(
            GITHUB_TOKEN_URL,
            headers={"Accept": "application/json"},
            data={
                "client_id": settings.github_oauth_client_id,
                "client_secret": settings.github_oauth_client_secret,
                "code": code,
                "redirect_uri": settings.github_oauth_redirect_uri,
            },
        )
        token_resp.raise_for_status()
        token_data = token_resp.json()
        if "access_token" not in token_data:
            raise HTTPException(status_code=400, detail=f"GitHub token exchange failed: {token_data}")
        access_token = token_data["access_token"]

        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/vnd.github+json"}
        user_resp = await client.get(GITHUB_USER_URL, headers=headers)
        user_resp.raise_for_status()
        gh_user = user_resp.json()

        email = gh_user.get("email")
        if not email:
            # Private-by-default emails don't show up on /user — fall back to
            # the verified-primary entry from /user/emails.
            emails_resp = await client.get(GITHUB_EMAILS_URL, headers=headers)
            if emails_resp.status_code == 200:
                for entry in emails_resp.json():
                    if entry.get("primary") and entry.get("verified"):
                        email = entry["email"]
                        break

    logger.info("GitHub login: id=%s", gh_user.get("id"))
    return await _login_via_identity(
        session,
        provider="github",
        provider_user_id=str(gh_user["id"]),
        email=email,
        display_name=gh_user.get("name") or gh_user.get("login"),
        invite_token=state_payload.get("invite_token"),
    )

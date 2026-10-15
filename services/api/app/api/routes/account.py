"""Desktop optional app sign-in: a Google/GitHub identity for the local user.

Identity only, not access control: the app still signs the local user in
automatically (/auth/local). Signing in links one oauth_identities row to that
user and sets their email/display name; signing out removes the link and
restores the local placeholder email. Desktop mode only (404 otherwise).
Never log identities from this module.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes import connect
from app.api.routes.auth import LOCAL_EMAIL
from app.core import desktop_state
from app.core.auth import AuthContext, require_auth, require_role
from app.core.config import settings
from app.core.db import get_session
from app.models.rag import OAuthIdentity, User

IDENTITY_PROVIDERS = ("google", "github")
COLLISION = "That account is already linked to another profile on this computer."
LOCAL_CLASH = "Another local profile already exists on this computer."


def _desktop_only() -> None:
    if settings.app_mode != "desktop":
        raise HTTPException(status_code=404, detail="Not found")


async def _owner(auth: AuthContext = Depends(require_auth)) -> AuthContext:
    require_role(auth, "owner")
    return auth


# _desktop_only is listed first so server mode gets 404 before any auth check.
router = APIRouter(dependencies=[Depends(_desktop_only), Depends(_owner)])


def _display_name(name: str, email: str | None) -> str:
    if name:
        return name
    if email and email.split("@", 1)[0]:
        return email.split("@", 1)[0]
    return "You"


async def link_identity(session: AsyncSession, user_id: uuid.UUID, result: dict) -> str:
    """connect.IDENTITY_HANDLER: link a Worker identity result
    ({provider, provider_user_id, email|None, name}) to the local user.
    Returns the display name; raises ValueError(message) to refuse."""
    provider = result.get("provider")
    provider_user_id = str(result.get("provider_user_id") or "").strip()
    if provider not in IDENTITY_PROVIDERS or not provider_user_id:
        raise ValueError("Couldn't finish signing in. Start again from the app.")
    email = str(result.get("email") or "").strip() or None
    name = _display_name(str(result.get("name") or "").strip(), email)

    user = await session.get(User, user_id)
    if user is None:
        raise ValueError("Couldn't finish signing in. Start again from the app.")

    existing = (
        await session.execute(
            select(OAuthIdentity).where(
                OAuthIdentity.provider == provider, OAuthIdentity.provider_user_id == provider_user_id
            )
        )
    ).scalar_one_or_none()
    if existing is not None and existing.user_id != user_id:
        raise ValueError(COLLISION)
    if email is None and user.email != LOCAL_EMAIL:
        # No email from this provider: fall back to the local placeholder (as sign-out does), never keep the
        # previous provider's email. Same uniqueness guard as sign-out.
        clash = (
            await session.execute(select(User.id).where(User.email == LOCAL_EMAIL, User.id != user_id).limit(1))
        ).first()
        if clash is not None:
            raise ValueError(LOCAL_CLASH)
    if email is not None:
        taken = (
            await session.execute(
                select(User.id).where(func.lower(User.email) == email.lower(), User.id != user_id).limit(1)
            )
        ).first()
        if taken is not None:
            raise ValueError(COLLISION)

    # One identity at a time: signing in with the other provider replaces the link.
    others = select(OAuthIdentity).where(OAuthIdentity.user_id == user_id)
    if existing is not None:
        others = others.where(OAuthIdentity.id != existing.id)
    for row in (await session.execute(others)).scalars().all():
        await session.delete(row)
    await session.flush()

    if existing is None:
        session.add(
            OAuthIdentity(
                id=uuid.uuid4(), provider=provider, provider_user_id=provider_user_id, user_id=user_id, email=email
            )
        )
    else:
        existing.email = email

    user.email = email if email is not None else LOCAL_EMAIL
    user.display_name = name
    user.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return name


connect.IDENTITY_HANDLER = link_identity


class AccountStatus(BaseModel):
    signed_in: bool
    provider: str | None
    email: str | None
    name: str | None
    broker_configured: bool
    prompt_seen: bool


@router.get("")
async def account_status(
    auth: AuthContext = Depends(_owner), session: AsyncSession = Depends(get_session)
) -> AccountStatus:
    identity = (
        await session.execute(
            select(OAuthIdentity)
            .where(OAuthIdentity.user_id == auth.user_id)
            .order_by(OAuthIdentity.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    user = await session.get(User, auth.user_id) if identity is not None else None
    return AccountStatus(
        signed_in=identity is not None,
        provider=identity.provider if identity else None,
        email=identity.email if identity else None,
        name=user.display_name if user else None,
        broker_configured=bool(settings.auth_broker_url),
        prompt_seen=bool(desktop_state.load_config().get("account_prompt_seen", False)),
    )


@router.post("/sign-out")
async def sign_out(auth: AuthContext = Depends(_owner), session: AsyncSession = Depends(get_session)) -> dict:
    """Remove the identity link and restore the local placeholder profile.
    Documents, chats and connections are untouched."""
    user = await session.get(User, auth.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Not found")
    if user.email != LOCAL_EMAIL:
        clash = (
            await session.execute(select(User.id).where(User.email == LOCAL_EMAIL, User.id != user.id).limit(1))
        ).first()
        if clash is not None:
            raise HTTPException(status_code=409, detail=LOCAL_CLASH)
    await session.execute(delete(OAuthIdentity).where(OAuthIdentity.user_id == user.id))
    user.email = LOCAL_EMAIL
    user.display_name = None
    user.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return {}


@router.post("/prompt-seen")
async def prompt_seen(auth: AuthContext = Depends(_owner)) -> dict:
    desktop_state.save_config(account_prompt_seen=True)
    return {}

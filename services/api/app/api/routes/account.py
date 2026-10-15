"""Desktop optional app sign-in: Google/GitHub identities for the local user.

Identity only, not access control: the app still signs the local user in
automatically (/auth/local). Signing in links oauth_identities rows (at most
one per provider) to that user and sets their email/display name.

"Logged in" = at least one identity row AND desktop.json has no
`account_signed_out`. Signing out keeps the identity rows, saves the profile's
name/email under `account_signed_out` and restores the local placeholder; a
later sign-in with a kept identity, or with a verified email matching the saved
one or a kept identity's, restores that profile. Unlinking deletes one
identity. Desktop mode only (404 otherwise). Never log identities or emails
from this module.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
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


SIGNED_OUT_KEY = "account_signed_out"


def _saved_profile() -> dict | None:
    """The profile saved by sign-out, or None when not signed out."""
    saved = desktop_state.load_config().get(SIGNED_OUT_KEY)
    return saved if isinstance(saved, dict) else None


def _real_email(value) -> str | None:
    """A usable email: non-empty and not the local placeholder."""
    value = str(value or "").strip()
    return value if value and value.lower() != LOCAL_EMAIL else None


async def _identities(session: AsyncSession, user_id: uuid.UUID) -> list[OAuthIdentity]:
    return list(
        (
            await session.execute(
                select(OAuthIdentity)
                .where(OAuthIdentity.user_id == user_id)
                .order_by(OAuthIdentity.created_at, OAuthIdentity.id)
            )
        )
        .scalars()
        .all()
    )


async def _email_taken(session: AsyncSession, email: str, user_id: uuid.UUID) -> bool:
    row = (
        await session.execute(
            select(User.id).where(func.lower(User.email) == email.lower(), User.id != user_id).limit(1)
        )
    ).first()
    return row is not None


async def _local_email_taken(session: AsyncSession, user_id: uuid.UUID) -> bool:
    row = (
        await session.execute(select(User.id).where(User.email == LOCAL_EMAIL, User.id != user_id).limit(1))
    ).first()
    return row is not None


async def link_identity(session: AsyncSession, user_id: uuid.UUID, result: dict) -> str:
    """connect.IDENTITY_HANDLER: link a Worker identity result
    ({provider, provider_user_id, email|None, name}) to the local user.
    The Worker only ever returns provider-verified emails (or None).
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
    if email is not None and await _email_taken(session, email, user_id):
        raise ValueError(COLLISION)

    kept = await _identities(session, user_id)
    saved = _saved_profile()

    if kept and saved is None:
        # Logged in: add (or update) this provider alongside the others; the profile stays as it is.
        new_email = email if user.email == LOCAL_EMAIL and email is not None else user.email
        new_name = user.display_name or name
        drop = []
    elif kept:
        # Signed out with identities kept: restore that profile if this sign-in is the same person.
        wanted = {e.lower() for e in [_real_email(saved.get("email")), *(i.email for i in kept)] if _real_email(e)}
        match = existing is not None or (email is not None and email.lower() in wanted)
        if match:
            new_email = _real_email(saved.get("email")) or email or LOCAL_EMAIL
            new_name = str(saved.get("name") or "").strip() or name
            if new_email != LOCAL_EMAIL and await _email_taken(session, new_email, user_id):
                raise ValueError(COLLISION)
            drop = []
        else:
            new_email, new_name, drop = email or LOCAL_EMAIL, name, kept
    else:
        new_email, new_name, drop = email or LOCAL_EMAIL, name, []

    if new_email == LOCAL_EMAIL and user.email != LOCAL_EMAIL and await _local_email_taken(session, user_id):
        raise ValueError(LOCAL_CLASH)

    # At most one identity per provider: another account of the same provider replaces it.
    drop = drop + [i for i in kept if i.provider == provider and i is not existing and i not in drop]
    for row in drop:
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

    user.email = new_email
    user.display_name = new_name
    user.updated_at = datetime.now(timezone.utc)
    await session.commit()
    if saved is not None:
        desktop_state.save_config(**{SIGNED_OUT_KEY: None})
    return new_name


connect.IDENTITY_HANDLER = link_identity


class LinkedIdentity(BaseModel):
    provider: str
    email: str | None


class AccountStatus(BaseModel):
    signed_in: bool
    provider: str | None
    email: str | None
    name: str | None
    identities: list[LinkedIdentity]
    broker_configured: bool
    prompt_seen: bool


@router.get("")
async def account_status(
    auth: AuthContext = Depends(_owner), session: AsyncSession = Depends(get_session)
) -> AccountStatus:
    identities = await _identities(session, auth.user_id)
    signed_in = bool(identities) and _saved_profile() is None
    user = await session.get(User, auth.user_id) if signed_in else None
    if user is None:
        signed_in, identities = False, []
    return AccountStatus(
        signed_in=signed_in,
        provider=identities[0].provider if identities else None,
        email=_real_email(user.email) if user else None,
        name=user.display_name if user else None,
        identities=[LinkedIdentity(provider=i.provider, email=i.email) for i in identities],
        broker_configured=bool(settings.auth_broker_url),
        prompt_seen=bool(desktop_state.load_config().get("account_prompt_seen", False)),
    )


async def _reset_to_local(session: AsyncSession, user: User) -> None:
    """Back to the unnamed local profile; 409 when another user holds the placeholder email."""
    if user.email != LOCAL_EMAIL and await _local_email_taken(session, user.id):
        raise HTTPException(status_code=409, detail=LOCAL_CLASH)
    user.email = LOCAL_EMAIL
    user.display_name = None
    user.updated_at = datetime.now(timezone.utc)


@router.post("/sign-out")
async def sign_out(auth: AuthContext = Depends(_owner), session: AsyncSession = Depends(get_session)) -> dict:
    """Restore the local placeholder profile, keeping the linked identities so
    signing in again with either one brings this profile back.
    Documents, chats and connections are untouched."""
    user = await session.get(User, auth.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Not found")
    logged_in = bool(await _identities(session, user.id)) and _saved_profile() is None
    profile = {"name": user.display_name, "email": user.email}
    await _reset_to_local(session, user)
    if logged_in:
        desktop_state.save_config(**{SIGNED_OUT_KEY: profile})
    await session.commit()
    return {}


@router.post("/unlink/{provider}")
async def unlink(
    provider: str, auth: AuthContext = Depends(_owner), session: AsyncSession = Depends(get_session)
) -> dict:
    """Remove one linked identity. Removing the last one signs out fully."""
    user = await session.get(User, auth.user_id)
    identities = await _identities(session, auth.user_id)
    gone = [i for i in identities if i.provider == provider]
    if user is None or provider not in IDENTITY_PROVIDERS or not gone:
        raise HTTPException(status_code=404, detail="Not found")
    remaining = [i for i in identities if i.provider != provider]
    if not remaining:
        await _reset_to_local(session, user)
    else:
        gone_emails = {i.email.lower() for i in gone if i.email}
        if user.email and user.email.lower() in gone_emails:
            replacement = next((i.email for i in remaining if i.email), None)
            if replacement is not None and not await _email_taken(session, replacement, user.id):
                user.email = replacement
                user.updated_at = datetime.now(timezone.utc)
    for row in gone:
        await session.delete(row)
    await session.commit()
    if not remaining and _saved_profile() is not None:
        desktop_state.save_config(**{SIGNED_OUT_KEY: None})
    return {}


@router.post("/prompt-seen")
async def prompt_seen(auth: AuthContext = Depends(_owner)) -> dict:
    desktop_state.save_config(account_prompt_seen=True)
    return {}

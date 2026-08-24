import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    AuthContext,
    create_access_token,
    hash_password,
    hash_service_token,
    require_auth,
    require_role,
    verify_password,
)
from app.core.config import settings
from app.core.db import get_session
from app.core.ratelimit import check_rate_limit
from app.models.rag import Organization, OrgInvite, OrgMember, ServiceToken, User

router = APIRouter()

COOKIE_NAME = "session"


def _set_session_cookie(response: Response, token: str) -> None:
    # SameSite=None (not Lax) because the frontend/API are different origins
    # even in local dev (localhost:3000 vs localhost:8000) — Lax cookies are
    # not sent on cross-site fetch/XHR. Secure works over plain http://localhost
    # since browsers treat localhost as a secure context; this needs revisiting
    # (unify origins behind one reverse proxy, or real TLS) before any
    # deployment reachable at a non-localhost domain.
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=True,
        samesite="none",
        max_age=settings.jwt_expires_seconds,
    )


class SignupRequest(BaseModel):
    email: str
    password: str
    display_name: str | None = None
    org_name: str | None = None  # creates a new org, caller becomes owner
    invite_token: str | None = None  # redeems a pending org_invites row instead


class LoginRequest(BaseModel):
    email: str
    password: str


class InviteRequest(BaseModel):
    email: str
    role: str = "member"


class InviteResponse(BaseModel):
    token: str
    expires_at: datetime


class MeResponse(BaseModel):
    user_id: str
    org_id: str
    org_name: str
    role: str
    email: str
    display_name: str | None


def _slugify(name: str) -> str:
    base = "-".join(name.strip().lower().split()) or "org"
    return f"{base}-{secrets.token_hex(4)}"


@router.post("/signup", status_code=201)
async def signup(
    request: SignupRequest, response: Response, session: AsyncSession = Depends(get_session)
) -> MeResponse:
    existing = await session.execute(select(User).where(User.email == request.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Email already registered")

    if bool(request.org_name) == bool(request.invite_token):
        raise HTTPException(status_code=400, detail="Provide exactly one of org_name or invite_token")

    user = User(
        id=uuid.uuid4(),
        email=request.email,
        password_hash=hash_password(request.password),
        display_name=request.display_name,
    )
    session.add(user)
    await session.flush()

    if request.org_name:
        org = Organization(id=uuid.uuid4(), name=request.org_name, slug=_slugify(request.org_name))
        session.add(org)
        await session.flush()
        role = "owner"
        org_id = org.id
    else:
        invite_result = await session.execute(
            select(OrgInvite).where(OrgInvite.token == request.invite_token, OrgInvite.accepted_at.is_(None))
        )
        invite = invite_result.scalar_one_or_none()
        if invite is None or invite.expires_at < datetime.now(timezone.utc):
            raise HTTPException(status_code=400, detail="Invalid or expired invite")
        if invite.email.lower() != request.email.lower():
            raise HTTPException(status_code=400, detail="Invite was issued to a different email")
        invite.accepted_at = datetime.now(timezone.utc)
        role = invite.role
        org_id = invite.org_id
        org = await session.get(Organization, org_id)

    session.add(OrgMember(id=uuid.uuid4(), org_id=org_id, user_id=user.id, role=role))
    await session.commit()

    token = create_access_token(user.id, org_id, role)
    _set_session_cookie(response, token)
    return MeResponse(
        user_id=str(user.id),
        org_id=str(org_id),
        org_name=org.name,
        role=role,
        email=user.email,
        display_name=user.display_name,
    )


@router.post("/login")
async def login(request: LoginRequest, response: Response, session: AsyncSession = Depends(get_session)) -> MeResponse:
    # Keyed by submitted email, not IP — throttles credential-stuffing against
    # one account regardless of source address.
    await check_rate_limit(f"login:{request.email.lower()}", settings.rate_limit_login_per_minute)

    result = await session.execute(select(User).where(User.email == request.email))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(request.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    membership_result = await session.execute(
        select(OrgMember).where(OrgMember.user_id == user.id).order_by(OrgMember.created_at)
    )
    memberships = membership_result.scalars().all()
    if not memberships:
        raise HTTPException(status_code=403, detail="Account has no organization")
    # A user in exactly one org is auto-selected; a user in multiple orgs is
    # logged into the first (earliest-joined) and can switch via /auth/select-org.
    active = memberships[0]
    org = await session.get(Organization, active.org_id)

    token = create_access_token(user.id, active.org_id, active.role)
    _set_session_cookie(response, token)
    return MeResponse(
        user_id=str(user.id),
        org_id=str(active.org_id),
        org_name=org.name,
        role=active.role,
        email=user.email,
        display_name=user.display_name,
    )


class SelectOrgRequest(BaseModel):
    org_id: uuid.UUID


@router.post("/select-org")
async def select_org(
    request: SelectOrgRequest,
    response: Response,
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> MeResponse:
    result = await session.execute(
        select(OrgMember, User)
        .join(User, User.id == OrgMember.user_id)
        .where(OrgMember.user_id == auth.user_id, OrgMember.org_id == request.org_id)
    )
    row = result.first()
    if row is None:
        raise HTTPException(status_code=403, detail="Not a member of that organization")
    member, user = row
    org = await session.get(Organization, member.org_id)

    token = create_access_token(user.id, member.org_id, member.role)
    _set_session_cookie(response, token)
    return MeResponse(
        user_id=str(user.id),
        org_id=str(member.org_id),
        org_name=org.name,
        role=member.role,
        email=user.email,
        display_name=user.display_name,
    )


@router.post("/logout", status_code=204)
async def logout(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME)


@router.get("/me")
async def me(auth: AuthContext = Depends(require_auth), session: AsyncSession = Depends(get_session)) -> MeResponse:
    user = await session.get(User, auth.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    org = await session.get(Organization, auth.org_id)
    if org is None:
        raise HTTPException(status_code=500, detail="Organization not found")
    return MeResponse(
        user_id=str(auth.user_id),
        org_id=str(auth.org_id),
        org_name=org.name,
        role=auth.role,
        email=user.email,
        display_name=user.display_name,
    )


@router.post("/invite")
async def invite(
    request: InviteRequest, auth: AuthContext = Depends(require_auth), session: AsyncSession = Depends(get_session)
) -> InviteResponse:
    require_role(auth, "owner", "admin")

    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    session.add(
        OrgInvite(
            id=uuid.uuid4(),
            org_id=auth.org_id,
            email=request.email,
            role=request.role,
            token=token,
            expires_at=expires_at,
        )
    )
    await session.commit()
    # No email sending in v1 — the token is returned directly for manual sharing.
    return InviteResponse(token=token, expires_at=expires_at)


class CreateServiceTokenRequest(BaseModel):
    label: str


class ServiceTokenCreated(BaseModel):
    id: str
    label: str
    token: str  # plaintext, shown once — only the sha256 hash is persisted


class ServiceTokenOut(BaseModel):
    id: str
    label: str
    created_at: datetime
    revoked_at: datetime | None


@router.post("/service-tokens", status_code=201)
async def create_service_token(
    request: CreateServiceTokenRequest,
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> ServiceTokenCreated:
    require_role(auth, "owner", "admin")

    token = secrets.token_urlsafe(32)
    record = ServiceToken(
        id=uuid.uuid4(), org_id=auth.org_id, token_hash=hash_service_token(token), label=request.label
    )
    session.add(record)
    await session.commit()
    return ServiceTokenCreated(id=str(record.id), label=record.label, token=token)


@router.get("/service-tokens")
async def list_service_tokens(
    auth: AuthContext = Depends(require_auth), session: AsyncSession = Depends(get_session)
) -> list[ServiceTokenOut]:
    require_role(auth, "owner", "admin")

    result = await session.execute(
        select(ServiceToken).where(ServiceToken.org_id == auth.org_id).order_by(ServiceToken.created_at.desc())
    )
    return [
        ServiceTokenOut(id=str(t.id), label=t.label, created_at=t.created_at, revoked_at=t.revoked_at)
        for t in result.scalars().all()
    ]


@router.delete("/service-tokens/{token_id}", status_code=204)
async def revoke_service_token(
    token_id: uuid.UUID, auth: AuthContext = Depends(require_auth), session: AsyncSession = Depends(get_session)
) -> None:
    require_role(auth, "owner", "admin")

    result = await session.execute(
        select(ServiceToken).where(ServiceToken.id == token_id, ServiceToken.org_id == auth.org_id)
    )
    token = result.scalar_one_or_none()
    if token is None:
        raise HTTPException(status_code=404, detail="Service token not found")
    token.revoked_at = datetime.now(timezone.utc)
    await session.commit()

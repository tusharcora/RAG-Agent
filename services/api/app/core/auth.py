import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Cookie, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.models.rag import ServiceToken


@dataclass
class AuthContext:
    org_id: uuid.UUID
    role: str  # 'owner' | 'admin' | 'member' | 'service' (see require_auth_or_service_token)
    user_id: uuid.UUID | None = None


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def create_access_token(user_id: uuid.UUID, org_id: uuid.UUID, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "org_id": str(org_id),
        "role": role,
        "iat": now,
        "exp": now + timedelta(seconds=settings.jwt_expires_seconds),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> AuthContext:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired session") from exc
    return AuthContext(user_id=uuid.UUID(payload["sub"]), org_id=uuid.UUID(payload["org_id"]), role=payload["role"])


async def require_auth(session: str | None = Cookie(default=None)) -> AuthContext:
    """FastAPI dependency: resolves the logged-in user/org/role from the
    httpOnly `session` cookie set by POST /auth/login. Replaces the old
    require_api_key/X-API-Key mechanism everywhere except the org-scoped
    service_tokens path used by /sync automation (see routes/sync.py)."""
    if session is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return decode_access_token(session)


def require_role(auth: AuthContext, *roles: str) -> None:
    if auth.role not in roles:
        raise HTTPException(status_code=403, detail="Insufficient permissions")


def hash_service_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def require_auth_or_service_token(
    session: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_session),
) -> AuthContext:
    """FastAPI dependency for /sync: accepts either a logged-in user's session
    cookie, or an org-scoped service_tokens bearer token for non-interactive
    automation (a scheduler, a customer's CI). org_id always comes from
    whichever credential resolves — never from a client-supplied parameter —
    so there's no "pass org_id and be trusted" step for anyone to get wrong.
    Replaces the old global API_SHARED_SECRET, which had no such scoping and
    would grant cross-org access from a single leaked value."""
    if session is not None:
        return decode_access_token(session)

    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1]
        result = await db.execute(
            select(ServiceToken).where(
                ServiceToken.token_hash == hash_service_token(token), ServiceToken.revoked_at.is_(None)
            )
        )
        service_token = result.scalar_one_or_none()
        if service_token is None:
            raise HTTPException(status_code=401, detail="Invalid or revoked service token")
        return AuthContext(org_id=service_token.org_id, role="service")

    raise HTTPException(status_code=401, detail="Not authenticated")

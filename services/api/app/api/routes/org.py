from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthContext, require_auth
from app.core.db import get_session
from app.models.rag import OrgMember, User

router = APIRouter(dependencies=[Depends(require_auth)])


class OrgMemberOut(BaseModel):
    user_id: str
    email: str
    display_name: str | None
    role: str


@router.get("/members")
async def list_org_members(
    auth: AuthContext = Depends(require_auth), session: AsyncSession = Depends(get_session)
) -> list[OrgMemberOut]:
    """Any authenticated org member can list co-members — used by the
    connections admin UI to render a member picker for restricted connections."""
    result = await session.execute(
        select(OrgMember, User).join(User, User.id == OrgMember.user_id).where(OrgMember.org_id == auth.org_id)
    )
    return [
        OrgMemberOut(user_id=str(user.id), email=user.email, display_name=user.display_name, role=member.role)
        for member, user in result.all()
    ]

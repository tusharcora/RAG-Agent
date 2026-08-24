from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.auth import AuthContext, require_auth, require_role
from app.core.config import settings
from app.core.redis import get_redis
from app.integrations.voyage import VOYAGE_TOKENS_USED_KEY

router = APIRouter(dependencies=[Depends(require_auth)])


class VoyageUsage(BaseModel):
    used: int
    budget: int
    percent: float


@router.get("/voyage")
async def voyage_usage(auth: AuthContext = Depends(require_auth)) -> VoyageUsage:
    # Billing/operational info, not something a regular member needs — same
    # owner/admin gate as the other admin-only connection settings.
    require_role(auth, "owner", "admin")

    used = int(await get_redis().get(VOYAGE_TOKENS_USED_KEY) or 0)
    budget = settings.voyage_free_tier_token_budget
    percent = (used / budget * 100) if budget else 0.0
    return VoyageUsage(used=used, budget=budget, percent=percent)

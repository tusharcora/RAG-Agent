from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness/readiness probe. Extend with DB/queue/redis pings once those
    are wired in per-project, so this becomes a real dependency health check."""
    return {"status": "ok"}

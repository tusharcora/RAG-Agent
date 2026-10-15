"""Desktop first-run setup: Ollama status and model downloads (desktop mode only)."""

import json
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core import desktop_state
from app.core.auth import AuthContext, require_auth, require_role
from app.core.config import settings
from app.integrations import ollama

logger = logging.getLogger(__name__)


def _desktop_only() -> None:
    if settings.app_mode != "desktop":
        raise HTTPException(status_code=404, detail="Not found")


async def _owner(auth: AuthContext = Depends(require_auth)) -> AuthContext:
    require_role(auth, "owner")
    return auth


# _desktop_only is listed first so server mode gets 404 before any auth check.
router = APIRouter(dependencies=[Depends(_desktop_only), Depends(_owner)])

# Models currently being downloaded (one pull per model at a time).
_IN_FLIGHT: set[str] = set()


class OllamaStatus(BaseModel):
    running: bool
    models: list[str]


class SetupStatus(BaseModel):
    setup_complete: bool
    embedding_provider: str
    ollama: OllamaStatus
    required_models: list[str]
    answer_model_candidates: list[str]


class PullRequest(BaseModel):
    model: str


def _required_models() -> list[str]:
    return [settings.ollama_embed_model, settings.ollama_chat_model]


def _candidates(models: list[str]) -> list[str]:
    return [m for m in models if "embed" not in m]


def _sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


@router.get("/status")
async def status() -> SetupStatus:
    models = await ollama.list_models()
    config = desktop_state.load_config()
    return SetupStatus(
        setup_complete=bool(config["setup_complete"]),
        embedding_provider=str(config["embedding_provider"]),
        ollama=OllamaStatus(running=models is not None, models=models or []),
        required_models=_required_models(),
        answer_model_candidates=_candidates(models or []),
    )


@router.post("/pull")
async def pull(body: PullRequest) -> StreamingResponse:
    model = body.model
    if model not in _required_models():
        installed = await ollama.list_models() or []
        if model not in _candidates(installed):
            raise HTTPException(status_code=400, detail=f"Unknown model {model}.")
    if model in _IN_FLIGHT:
        raise HTTPException(status_code=409, detail=f"Already downloading {model}.")
    _IN_FLIGHT.add(model)  # no await between the check and the add

    async def stream():
        try:
            async for line in ollama.pull(model):
                if line.get("error"):
                    yield _sse("error", {"message": str(line["error"])})
                    return
                yield _sse(
                    "progress",
                    {
                        "completed": line.get("completed", 0),
                        "total": line.get("total", 0),
                        "status": line.get("status", ""),
                    },
                )
            yield _sse("done", {})
        except httpx.HTTPError as exc:
            logger.warning("model pull failed: %s", exc)
            yield _sse("error", {"message": "Could not reach Ollama. Is it still running?"})
        finally:
            _IN_FLIGHT.discard(model)

    return StreamingResponse(stream(), media_type="text/event-stream")

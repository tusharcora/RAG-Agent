"""Desktop first-run setup: Ollama status and model downloads (desktop mode only)."""

import json
import logging
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core import desktop_state
from app.core.auth import AuthContext, require_auth, require_role
from app.api.routes.llm_settings import LlmSettingsIn, apply_llm_choice, check_llm
from app.core.config import settings
from app.core.db import get_session
from app.integrations import ollama, voyage

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
    # The installed name of the search model under any tag (e.g.
    # "nomic-embed-text:v1.5"), or None when it still has to be downloaded.
    embed_model: str | None = None


class PullRequest(BaseModel):
    model: str


class CompleteLocal(BaseModel):
    answer_model: str


class CompleteKeys(BaseModel):
    voyage_api_key: str
    provider: Literal["anthropic", "openai", "gemini"]
    api_key: str
    model: str | None = None


def _required_models() -> list[str]:
    return [settings.ollama_embed_model, settings.ollama_chat_model]


def _installed_as(model: str, installed: list[str]) -> str | None:
    """The installed name for `model`: an exact match first, otherwise the same
    model under another tag ("nomic-embed-text" matches "nomic-embed-text:v1.5").
    `installed` comes from ollama.list_models(), with ':latest' already stripped."""
    base = model.removesuffix(":latest")
    if base in installed:
        return base
    if ":" in base:
        return None
    return next((m for m in installed if m.split(":", 1)[0] == base), None)


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
        embed_model=_installed_as(settings.ollama_embed_model, models or []),
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


@router.post("/complete-local")
async def complete_local(
    body: CompleteLocal, auth: AuthContext = Depends(_owner), session: AsyncSession = Depends(get_session)
) -> dict:
    installed = await ollama.list_models() or []
    embed_model = _installed_as(settings.ollama_embed_model, installed)
    if embed_model is None:
        raise HTTPException(status_code=400, detail=f"{settings.ollama_embed_model} isn't installed in Ollama yet.")
    if body.answer_model.removesuffix(":latest") not in installed:
        raise HTTPException(status_code=400, detail=f"{body.answer_model} isn't installed in Ollama yet.")
    await apply_llm_choice(
        session,
        auth.org_id,
        LlmSettingsIn(provider="local", model=body.answer_model, base_url=f"{settings.ollama_url}/v1"),
    )
    desktop_state.save_config(setup_complete=True, embedding_provider="ollama", ollama_embed_model=embed_model)
    desktop_state.schedule_restart()
    return {"restarting": True}


def _bad(field: str, message: str) -> HTTPException:
    return HTTPException(status_code=400, detail={"field": field, "message": message})


def _default_model(provider: str) -> str:
    return {"anthropic": "claude-sonnet-5-5", "openai": "gpt-5-mini", "gemini": settings.query_model}[provider]


async def _gemini_models(api_key: str) -> list[str]:
    from google import genai

    client = genai.Client(api_key=api_key)
    return [m.name.removeprefix("models/") async for m in await client.aio.models.list()]


@router.post("/complete-keys")
async def complete_keys(
    body: CompleteKeys, auth: AuthContext = Depends(_owner), session: AsyncSession = Depends(get_session)
) -> dict:
    # Validate everything first; nothing is written until every check passes.
    voyage_key, api_key = body.voyage_api_key.strip(), body.api_key.strip()
    model = (body.model or "").strip() or _default_model(body.provider)
    if not voyage_key:
        raise _bad("voyage_api_key", "Enter your Voyage API key.")
    if not api_key:
        raise _bad("api_key", "Enter your API key.")
    try:
        await voyage.check_key(voyage_key)
    except ValueError as exc:
        raise _bad("voyage_api_key", str(exc))
    except httpx.HTTPError as exc:
        raise _bad("voyage_api_key", f"Could not reach Voyage: {exc}")

    request = LlmSettingsIn(provider=body.provider, model=model, api_key=api_key)
    if body.provider == "gemini":
        try:
            models = await _gemini_models(api_key)
        except Exception as exc:  # google-genai raises its own APIError types
            raise _bad("api_key", f"Gemini rejected this key: {str(exc)[:200]}")
        if model not in models:
            raise _bad("model", f"Gemini has no model {model!r}. Available: {', '.join(models)}")
    else:
        result = await check_llm(session, auth.org_id, request)
        if not result.ok:
            if result.models:
                raise _bad("model", f"{result.error} Available: {', '.join(result.models)}")
            raise _bad("api_key", result.error or "The key didn't work.")

    if body.provider == "gemini":
        desktop_state.save_secrets(google_api_key=api_key)
        await apply_llm_choice(session, auth.org_id, LlmSettingsIn(provider="gemini"))
    else:
        await apply_llm_choice(session, auth.org_id, request)
    desktop_state.save_secrets(voyage_api_key=voyage_key)
    desktop_state.save_config(setup_complete=True, embedding_provider="voyage")
    desktop_state.schedule_restart()
    return {"restarting": True}

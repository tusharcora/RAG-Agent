"""/settings/llm — which model writes this org's chat answers.

The active provider lives in org_llm_settings; each provider's key, model and
URL live in org_llm_credentials and are kept when the active provider changes,
so an admin can switch back and forth without re-entering keys. Keys are
write-only: stored encrypted, only a short hint is ever returned. Owners and
admins change settings; everyone in the org can see what's in use."""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthContext, require_auth, require_role
from app.core.config import settings
from app.core.crypto import decrypt_token, encrypt_token
from app.core.db import get_session
from app.core.llm import PROVIDERS, LlmConfig, LlmError, list_models, stream_answer
from app.models.rag import OrgLlmCredential, OrgLlmSettings

logger = logging.getLogger(__name__)
router = APIRouter()

CONFIGURABLE = ("anthropic", "openai", "local")


class ProviderConfigOut(BaseModel):
    model: str | None
    base_url: str | None
    has_api_key: bool
    api_key_hint: str | None


class LlmSettingsOut(BaseModel):
    provider: str
    default_model: str
    providers: dict[str, ProviderConfigOut]
    # Convenience mirrors of the active provider's config.
    model: str | None
    base_url: str | None
    has_api_key: bool
    api_key_hint: str | None


class LlmSettingsIn(BaseModel):
    provider: str
    model: str | None = Field(default=None, max_length=200)
    base_url: str | None = Field(default=None, max_length=500)
    # None or "" = keep the stored key. DELETE /settings/llm/keys/{provider} forgets a provider.
    api_key: str | None = Field(default=None, max_length=500)


class TestResult(BaseModel):
    ok: bool
    models: list[str] = []
    error: str | None = None
    sample: str | None = None  # the model's reply to a one-word test prompt


async def _load(session: AsyncSession, org_id) -> tuple[OrgLlmSettings | None, dict[str, OrgLlmCredential]]:
    active = await session.get(OrgLlmSettings, org_id)
    result = await session.execute(select(OrgLlmCredential).where(OrgLlmCredential.org_id == org_id))
    return active, {c.provider: c for c in result.scalars().all()}


def _out(active: OrgLlmSettings | None, creds: dict[str, OrgLlmCredential]) -> LlmSettingsOut:
    providers = {
        p: ProviderConfigOut(
            model=creds[p].model if p in creds else None,
            base_url=creds[p].base_url if p in creds else None,
            has_api_key=bool(p in creds and creds[p].api_key_encrypted),
            api_key_hint=creds[p].api_key_hint if p in creds else None,
        )
        for p in CONFIGURABLE
    }
    provider = active.provider if active else "gemini"
    current = providers.get(provider)
    return LlmSettingsOut(
        provider=provider,
        default_model=settings.query_model,
        providers=providers,
        model=current.model if current else None,
        base_url=current.base_url if current else None,
        has_api_key=current.has_api_key if current else False,
        api_key_hint=current.api_key_hint if current else None,
    )


def _clean(request: LlmSettingsIn) -> LlmSettingsIn:
    if request.provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"provider must be one of {', '.join(PROVIDERS)}")
    model = (request.model or "").strip() or None
    base_url = (request.base_url or "").strip().rstrip("/") or None
    if base_url and not base_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="The URL must start with http:// or https://")
    api_key = (request.api_key or "").strip() or None
    return LlmSettingsIn(provider=request.provider, model=model, base_url=base_url, api_key=api_key)


def _stored_key(cred: OrgLlmCredential | None) -> str | None:
    if cred is None or not cred.api_key_encrypted:
        return None
    try:
        return decrypt_token(cred.api_key_encrypted)
    except Exception:
        return None


@router.get("/llm")
async def get_llm_settings(
    auth: AuthContext = Depends(require_auth), session: AsyncSession = Depends(get_session)
) -> LlmSettingsOut:
    return _out(*await _load(session, auth.org_id))


@router.put("/llm")
async def put_llm_settings(
    request: LlmSettingsIn, auth: AuthContext = Depends(require_auth), session: AsyncSession = Depends(get_session)
) -> LlmSettingsOut:
    require_role(auth, "owner", "admin")
    request = _clean(request)
    active, creds = await _load(session, auth.org_id)

    if request.provider != "gemini":
        cred = creds.get(request.provider)
        key = request.api_key or _stored_key(cred)
        if request.provider in ("anthropic", "openai") and not key:
            raise HTTPException(status_code=400, detail="An API key is required for this provider.")
        if not request.model:
            raise HTTPException(status_code=400, detail="Choose a model.")
        if request.provider == "local" and not request.base_url:
            raise HTTPException(status_code=400, detail="Enter the model server's URL.")

        if cred is None:
            cred = OrgLlmCredential(org_id=auth.org_id, provider=request.provider)
            session.add(cred)
            creds[request.provider] = cred
        cred.model = request.model
        cred.base_url = request.base_url if request.provider in ("openai", "local") else None
        if request.api_key:
            cred.api_key_encrypted = encrypt_token(request.api_key)
            cred.api_key_hint = f"…{request.api_key[-4:]}" if len(request.api_key) >= 8 else "saved"

    if active is None:
        active = OrgLlmSettings(org_id=auth.org_id)
        session.add(active)
    active.provider = request.provider
    # The legacy per-row key columns are superseded by org_llm_credentials.
    active.model, active.base_url, active.api_key_encrypted, active.api_key_hint = None, None, None, None
    await session.commit()
    return _out(active, creds)


@router.delete("/llm/keys/{provider}")
async def delete_llm_key(
    provider: str, auth: AuthContext = Depends(require_auth), session: AsyncSession = Depends(get_session)
) -> LlmSettingsOut:
    require_role(auth, "owner", "admin")
    if provider not in CONFIGURABLE:
        raise HTTPException(status_code=404, detail="Unknown provider")
    active, creds = await _load(session, auth.org_id)
    cred = creds.pop(provider, None)
    if cred is not None:
        # Forgets everything saved for this provider: key, model and URL.
        await session.delete(cred)
        if active is not None and active.provider == provider:
            active.provider = "gemini"
        await session.commit()
    return _out(active, creds)


@router.post("/llm/test")
async def test_llm_settings(
    request: LlmSettingsIn, auth: AuthContext = Depends(require_auth), session: AsyncSession = Depends(get_session)
) -> TestResult:
    require_role(auth, "owner", "admin")
    request = _clean(request)
    _, creds = await _load(session, auth.org_id)
    cfg = LlmConfig(
        provider=request.provider,
        model=request.model,
        base_url=request.base_url,
        api_key=request.api_key or _stored_key(creds.get(request.provider)),
    )
    try:
        models = await list_models(cfg)
    except LlmError as exc:
        return TestResult(ok=False, error=str(exc))
    if cfg.model and models and cfg.model not in models:
        return TestResult(ok=False, models=models, error=f"Connected, but {cfg.model!r} isn't one of its models.")
    if cfg.provider == "gemini" or not cfg.model:
        return TestResult(ok=True, models=models)
    # A real round trip through the same streaming code chat answers use.
    try:
        sample = await asyncio.wait_for(_sample(cfg), timeout=90)
    except asyncio.TimeoutError:
        return TestResult(ok=False, models=models, error="Connected, but the model took over 90 seconds to reply.")
    except LlmError as exc:
        return TestResult(ok=False, models=models, error=str(exc))
    except Exception as exc:  # surfaced to the admin instead of a bare 500
        logger.exception("LLM test prompt failed (provider=%s)", cfg.provider)
        return TestResult(ok=False, models=models, error=f"{type(exc).__name__}: {exc}")
    return TestResult(ok=True, models=models, sample=sample)


async def _sample(cfg: LlmConfig) -> str:
    text = ""
    async for kind, value in stream_answer(cfg, "Reply with exactly: OK", [], "Say OK."):
        if kind == "delta":
            text += value
            if len(text) > 200:
                break
    return text.strip()[:200]

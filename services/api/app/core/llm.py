"""Answer-generation providers.

Each org picks the model that writes chat answers (org_llm_settings):
  gemini     the server's own Google AI Studio key (default)
  anthropic  the org's Anthropic API key (Claude models)
  openai     the org's OpenAI API key
  local      any OpenAI-compatible endpoint, e.g. Ollama or LM Studio

Consumer subscriptions (Claude Pro/Max, ChatGPT Plus) are deliberately not a
provider here: their logins may not be used by third-party apps. Those users
reach this knowledge base the other way round, through the MCP server.
"""

import json
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
from cryptography.fernet import InvalidToken
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.crypto import decrypt_token
from app.models.rag import OrgLlmCredential, OrgLlmSettings

logger = logging.getLogger(__name__)

PROVIDERS = ("gemini", "anthropic", "openai", "local")
OPENAI_URL = "https://api.openai.com/v1"
ANTHROPIC_VERSION = "2023-06-01"
MAX_OUTPUT_TOKENS = 4096

_genai_client: genai.Client | None = None


def _gemini() -> genai.Client:
    # Built on first use: the SDK refuses to construct without a key, and the
    # desktop app runs fine without one (answers come from a local model).
    global _genai_client
    if _genai_client is None:
        if not settings.google_api_key:
            raise LlmError("The built-in Gemini model needs a Google API key. Pick another model in Settings.")
        _genai_client = genai.Client(api_key=settings.google_api_key)
    return _genai_client


class LlmError(Exception):
    """A provider failed in a way worth showing to the user verbatim."""


@dataclass
class LlmConfig:
    provider: str = "gemini"
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None

    @property
    def label(self) -> str:
        return f"{self.provider} · {self.resolved_model}"

    @property
    def resolved_model(self) -> str:
        if self.provider == "gemini":
            return self.model or settings.query_model
        return self.model or "(no model set)"


async def load_llm_config(session: AsyncSession, org_id: uuid.UUID) -> LlmConfig:
    active = await session.get(OrgLlmSettings, org_id)
    if active is None and settings.app_mode == "desktop":
        # Desktop default: the local Ollama the app already uses for embeddings.
        return LlmConfig(provider="local", model=settings.ollama_chat_model, base_url=f"{settings.ollama_url}/v1")
    if active is None or active.provider == "gemini":
        return LlmConfig()
    cred = await session.get(OrgLlmCredential, (org_id, active.provider))
    if cred is None:
        raise LlmError(f"Nothing is saved for {active.provider}. Configure it in Settings.")
    try:
        api_key = decrypt_token(cred.api_key_encrypted)
    except InvalidToken:
        raise LlmError("The saved API key can't be decrypted. Re-enter it in Settings.")
    return LlmConfig(provider=active.provider, model=cred.model, base_url=cred.base_url, api_key=api_key)


# --- streaming ---------------------------------------------------------------
# Every provider yields ("delta", text) chunks and finally ("end", truncated).


async def stream_answer(
    cfg: LlmConfig, system: str, history: list[dict], user_turn: str
) -> AsyncIterator[tuple[str, object]]:
    if cfg.provider == "gemini":
        async for item in _stream_gemini(system, history, user_turn):
            yield item
    elif cfg.provider == "anthropic":
        async for item in _stream_anthropic(cfg, system, history, user_turn):
            yield item
    elif cfg.provider in ("openai", "local"):
        async for item in _stream_openai_compatible(cfg, system, history, user_turn):
            yield item
    else:
        raise LlmError(f"Unknown provider {cfg.provider!r}")


def _is_rate_limited(exc: BaseException) -> bool:
    return isinstance(exc, genai_errors.APIError) and getattr(exc, "code", None) == 429


@retry(
    retry=retry_if_exception(_is_rate_limited),
    wait=wait_exponential(multiplier=1, min=1, max=30),
    stop=stop_after_attempt(5),
    reraise=True,
)
async def _open_gemini_stream(system: str, contents: list[genai_types.Content]):
    # Only opening the stream is retried; once deltas may be on the wire a
    # failure surfaces as an error event instead of a silent retry.
    return await _gemini().aio.models.generate_content_stream(
        model=settings.query_model,
        contents=contents,
        config=genai_types.GenerateContentConfig(system_instruction=system, max_output_tokens=MAX_OUTPUT_TOKENS),
    )


async def _stream_gemini(system: str, history: list[dict], user_turn: str):
    # Gemini calls the assistant role "model"; stored history keeps "assistant".
    contents = [
        genai_types.Content(
            role="model" if turn["role"] == "assistant" else "user",
            parts=[genai_types.Part.from_text(text=turn["content"])],
        )
        for turn in history
    ]
    contents.append(genai_types.Content(role="user", parts=[genai_types.Part.from_text(text=user_turn)]))
    stream = await _open_gemini_stream(system, contents)
    truncated = False
    async for chunk in stream:
        if chunk.text:
            yield "delta", chunk.text
        if chunk.candidates and chunk.candidates[0].finish_reason == genai_types.FinishReason.MAX_TOKENS:
            truncated = True
    yield "end", truncated


def _chat_messages(history: list[dict], user_turn: str) -> list[dict]:
    messages = [{"role": t["role"], "content": t["content"]} for t in history if t["role"] in ("user", "assistant")]
    messages.append({"role": "user", "content": user_turn})
    return messages


async def _raise_for_status(resp: httpx.Response, provider: str) -> None:
    if resp.status_code < 400:
        return
    body = (await resp.aread()).decode(errors="replace")
    detail = body
    try:
        parsed = json.loads(body)
        err = parsed.get("error", parsed)
        detail = err.get("message") if isinstance(err, dict) else str(err)
    except (ValueError, AttributeError):
        pass
    raise LlmError(f"{provider} returned {resp.status_code}: {detail[:300]}")


async def _sse_events(resp: httpx.Response) -> AsyncIterator[tuple[str | None, str]]:
    event = None
    async for line in resp.aiter_lines():
        if not line:
            event = None
            continue
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            yield event, line[5:].strip()


async def _stream_anthropic(cfg: LlmConfig, system: str, history: list[dict], user_turn: str):
    if not cfg.api_key:
        raise LlmError("No Anthropic API key is saved.")
    if not cfg.model:
        raise LlmError("No Claude model is selected.")
    payload = {
        "model": cfg.model,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "system": system,
        "messages": _chat_messages(history, user_turn),
        "stream": True,
    }
    headers = {"x-api-key": cfg.api_key, "anthropic-version": ANTHROPIC_VERSION, "content-type": "application/json"}
    truncated = False
    async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=15)) as client:
        async with client.stream("POST", f"{settings.anthropic_base_url}/messages", json=payload, headers=headers) as resp:
            await _raise_for_status(resp, "Anthropic")
            async for event, data in _sse_events(resp):
                if not data:
                    continue
                msg = json.loads(data)
                kind = msg.get("type") or event
                if kind == "content_block_delta" and msg.get("delta", {}).get("type") == "text_delta":
                    yield "delta", msg["delta"]["text"]
                elif kind == "message_delta" and msg.get("delta", {}).get("stop_reason") == "max_tokens":
                    truncated = True
                elif kind == "error":
                    raise LlmError(f"Anthropic: {msg.get('error', {}).get('message', 'stream error')}")
    yield "end", truncated


def _openai_base(cfg: LlmConfig) -> str:
    if cfg.provider == "openai":
        return (cfg.base_url or OPENAI_URL).rstrip("/")
    if not cfg.base_url:
        raise LlmError("No local model URL is saved.")
    return cfg.base_url.rstrip("/")


def _openai_headers(cfg: LlmConfig) -> dict:
    headers = {"content-type": "application/json"}
    if cfg.api_key:
        headers["authorization"] = f"Bearer {cfg.api_key}"
    return headers


async def _stream_openai_compatible(cfg: LlmConfig, system: str, history: list[dict], user_turn: str):
    if cfg.provider == "openai" and not cfg.api_key:
        raise LlmError("No OpenAI API key is saved.")
    if not cfg.model:
        raise LlmError("No model is selected.")
    payload = {
        "model": cfg.model,
        "messages": [{"role": "system", "content": system}, *_chat_messages(history, user_turn)],
        "stream": True,
    }
    name = "OpenAI" if cfg.provider == "openai" else "Local model"
    truncated = False
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=15)) as client:
            async with client.stream(
                "POST", f"{_openai_base(cfg)}/chat/completions", json=payload, headers=_openai_headers(cfg)
            ) as resp:
                await _raise_for_status(resp, name)
                async for _event, data in _sse_events(resp):
                    if not data or data == "[DONE]":
                        continue
                    msg = json.loads(data)
                    if "error" in msg:
                        raise LlmError(f"{name}: {msg['error'].get('message', 'stream error')}")
                    choices = msg.get("choices") or []
                    if not choices:
                        continue
                    text = (choices[0].get("delta") or {}).get("content")
                    if text:
                        yield "delta", text
                    if choices[0].get("finish_reason") == "length":
                        truncated = True
    except httpx.ConnectError as exc:
        raise LlmError(f"{name} isn't reachable at {_openai_base(cfg)} ({exc}).") from exc
    yield "end", truncated


# --- connection test -----------------------------------------------------------


async def list_models(cfg: LlmConfig) -> list[str]:
    """Checks the credentials/endpoint and returns the model ids it offers."""
    if cfg.provider == "gemini":
        return [settings.query_model]
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=10)) as client:
            if cfg.provider == "anthropic":
                if not cfg.api_key:
                    raise LlmError("Enter an Anthropic API key first.")
                resp = await client.get(
                    f"{settings.anthropic_base_url}/models",
                    params={"limit": 100},
                    headers={"x-api-key": cfg.api_key, "anthropic-version": ANTHROPIC_VERSION},
                )
                await _raise_for_status(resp, "Anthropic")
                return [m["id"] for m in resp.json().get("data", [])]
            if cfg.provider == "openai" and not cfg.api_key:
                raise LlmError("Enter an OpenAI API key first.")
            resp = await client.get(f"{_openai_base(cfg)}/models", headers=_openai_headers(cfg))
            await _raise_for_status(resp, "OpenAI" if cfg.provider == "openai" else "Local model")
            return sorted(m["id"] for m in resp.json().get("data", []))
    except httpx.ConnectError as exc:
        raise LlmError(f"Couldn't reach {_openai_base(cfg) if cfg.provider != 'anthropic' else settings.anthropic_base_url}: {exc}") from exc
    except httpx.TimeoutException as exc:
        raise LlmError("The provider didn't answer within 20 seconds.") from exc

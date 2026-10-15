import json
from collections.abc import AsyncIterator

import httpx

from app.core.config import settings

# Tests set this to an httpx.MockTransport.
TRANSPORT: httpx.AsyncBaseTransport | None = None


async def list_models() -> list[str] | None:
    """Installed model names (':latest' stripped), or None if Ollama isn't reachable."""
    try:
        async with httpx.AsyncClient(transport=TRANSPORT, timeout=2.0) as client:
            resp = await client.get(f"{settings.ollama_url}/api/tags")
            resp.raise_for_status()
            models = resp.json().get("models", [])
    except (httpx.HTTPError, ValueError):
        return None
    return [m["name"].removesuffix(":latest") for m in models if isinstance(m, dict) and "name" in m]


async def pull(model: str) -> AsyncIterator[dict]:
    """Yield Ollama's progress lines (status, completed, total, error)."""
    async with httpx.AsyncClient(transport=TRANSPORT, timeout=None) as client:
        async with client.stream(
            "POST", f"{settings.ollama_url}/api/pull", json={"model": model, "stream": True}
        ) as resp:
            if resp.status_code >= 400:
                body = (await resp.aread()).decode(errors="replace")
                try:
                    msg = json.loads(body).get("error") or body
                except ValueError:
                    msg = body
                yield {"error": msg or f"Ollama returned {resp.status_code}"}
                return
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                try:
                    yield json.loads(line)
                except ValueError:
                    continue

"""Desktop Connect flow: OAuth through the sign-in Worker (desktop/broker).

start  -> the app keeps {purpose, verifier, user, org} in memory and hands the
          browser the Worker's /start URL with challenge = sha256(verifier).
finish -> the browser comes back here with `state` and the Worker's one-time
          `pickup`; we redeem the result from the Worker with the verifier
          (which never left this process) plus that pickup (which only this
          loopback redirect ever saw), and save the connection (notion/jira)
          or link the identity (google/github, via IDENTITY_HANDLER).

Desktop mode only (404 otherwise). Flows live in process memory, never on
disk, and are dropped lazily after FLOW_TTL seconds. Never log verifiers,
states, tokens or identities from this module.
"""

import base64
import hashlib
import html
import logging
import secrets
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthContext, require_auth, require_role
from app.core.config import settings
from app.core.db import get_session
from app.integrations import broker
from app.integrations.connections import save_jira_connection, save_notion_connection

logger = logging.getLogger(__name__)

FLOW_TTL = 600  # seconds
PURPOSES = ("notion", "jira", "google", "github")
IDENTITY_PURPOSES = ("google", "github")
DISPLAY_NAME = {"notion": "Notion", "jira": "Jira", "google": "Google", "github": "GitHub"}

NOT_CONFIGURED = "Sign-in service isn't configured."
CLOSE_TAB = "You can close this tab and go back to Knowledge Agent."
FAILED = "Couldn't finish signing in. Start again from the app."


@dataclass
class Flow:
    purpose: str
    verifier: str
    user_id: uuid.UUID | None
    org_id: uuid.UUID
    created: float  # time.monotonic()
    status: Literal["pending", "done", "error"] = "pending"
    message: str | None = None
    # Set while /finish is redeeming, so a second load of the finish page
    # can't race the first one into a redeem the Worker would refuse.
    redeeming: bool = False


_FLOWS: dict[str, Flow] = {}

# Filled by the app-sign-in work for google/github:
#   async def handler(session, user_id: uuid.UUID, result: dict) -> str
# returns the display name ("Signed in as {name}"), raises ValueError(message) to refuse.
IDENTITY_HANDLER: Callable[[AsyncSession, uuid.UUID, dict], Awaitable[str]] | None = None


def _desktop_only() -> None:
    if settings.app_mode != "desktop":
        raise HTTPException(status_code=404, detail="Not found")


async def _owner(auth: AuthContext = Depends(require_auth)) -> AuthContext:
    require_role(auth, "owner")
    return auth


# _desktop_only first so server mode gets 404 before any auth check. /finish
# has no auth dependency: the state is the credential there.
router = APIRouter(dependencies=[Depends(_desktop_only)])


def _prune() -> None:
    cutoff = time.monotonic() - FLOW_TTL
    for state in [s for s, f in _FLOWS.items() if f.created < cutoff]:
        _FLOWS.pop(state, None)


def _flow_for(state: str, auth: AuthContext) -> Flow:
    _prune()
    flow = _FLOWS.get(state)
    if flow is None or flow.org_id != auth.org_id:
        raise HTTPException(status_code=404, detail="Not found")
    return flow


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


@router.post("/{purpose}/start")
async def start(purpose: str, request: Request, auth: AuthContext = Depends(_owner)) -> dict:
    if purpose not in PURPOSES or (purpose in IDENTITY_PURPOSES and IDENTITY_HANDLER is None):
        raise HTTPException(status_code=404, detail="Not found")
    if not settings.auth_broker_url:
        raise HTTPException(status_code=409, detail=NOT_CONFIGURED)
    try:
        await broker.check_reachable()
    except broker.BrokerUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    _prune()
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(32)
    # The port this API is actually serving on (the launcher's port behind the desktop app).
    port = request.url.port or (443 if request.url.scheme == "https" else 80)
    _FLOWS[state] = Flow(
        purpose=purpose, verifier=verifier, user_id=auth.user_id, org_id=auth.org_id, created=time.monotonic()
    )
    return {"state": state, "url": broker.start_url(purpose, state, _challenge(verifier), port)}


def _page(headline: str, detail: str | None = None) -> HTMLResponse:
    detail_html = f"<p>{html.escape(detail)}</p>" if detail else ""
    body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Knowledge Agent</title>
<style>
:root{{color-scheme:light dark}}
body{{margin:0;font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;background:#f6f7f9;color:#1f2328}}
main{{max-width:30rem;margin:5rem auto;padding:2rem;background:#fff;border:1px solid #d8dee4;border-radius:12px}}
h1{{font-size:.85rem;font-weight:600;letter-spacing:.04em;text-transform:uppercase;color:#59636e;margin:0 0 1rem}}
.headline{{font-size:1.25rem;font-weight:600;margin:0 0 .5rem}}
p{{margin:0;color:#59636e}}
@media (prefers-color-scheme:dark){{
body{{background:#0d1117;color:#e6edf3}}
main{{background:#161b22;border-color:#30363d}}
h1,p{{color:#9198a1}}
}}
@media (max-width:34rem){{main{{margin:2rem 1rem;padding:1.5rem}}}}
</style></head>
<body><main><h1>Knowledge Agent</h1><div class="headline">{html.escape(headline)}</div>{detail_html}</main></body></html>"""
    return HTMLResponse(
        body,
        status_code=200,
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
        },
    )


def _fail(flow: Flow, message: str) -> HTMLResponse:
    flow.status, flow.message = "error", message
    return _page(message)


@router.get("/finish")
async def finish(state: str = "", pickup: str = "", session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    _prune()
    flow = _FLOWS.get(state)
    # No pickup: not a redirect from the Worker's callback. Leave the flow alone (don't burn it) and say expired.
    if flow is None or flow.status != "pending" or flow.redeeming or not pickup:
        return _page(broker.EXPIRED)

    flow.redeeming = True
    try:
        try:
            result = await broker.redeem(state, flow.verifier, pickup)
        except broker.BrokerError as exc:
            return _fail(flow, str(exc))

        if result.get("provider") != flow.purpose:
            logger.warning("sign-in result is for a different provider than this flow (%s)", flow.purpose)
            return _fail(flow, broker.EXPIRED)
        if "error" in result:
            return _fail(flow, str(result["error"]) or FAILED)

        name = DISPLAY_NAME[flow.purpose]
        try:
            if flow.purpose == "notion":
                await save_notion_connection(session, flow.org_id, result)
                headline = f"Connected to {name} ✓"
            elif flow.purpose == "jira":
                site = {"id": result["cloud_id"], "url": result["site_url"], "name": result.get("site_name")}
                await save_jira_connection(session, flow.org_id, result, site)
                headline = f"Connected to {name} ✓"
            else:
                handler = IDENTITY_HANDLER
                if handler is None or flow.user_id is None:
                    return _fail(flow, FAILED)
                try:
                    display = await handler(session, flow.user_id, result)
                except ValueError as exc:
                    return _fail(flow, str(exc) or FAILED)
                headline = f"Signed in as {display}"
        except Exception as exc:  # noqa: BLE001 - the page must not 500; keep tokens out of the log
            logger.error("saving the %s sign-in failed: %s", flow.purpose, type(exc).__name__)
            await session.rollback()
            return _fail(flow, FAILED)

        flow.status, flow.message = "done", headline
        return _page(headline, CLOSE_TAB)
    finally:
        flow.redeeming = False


@router.get("/status/{state}")
async def status(state: str, auth: AuthContext = Depends(_owner)) -> dict:
    flow = _flow_for(state, auth)
    return {"status": flow.status, "message": flow.message}


@router.post("/{state}/cancel")
async def cancel(state: str, auth: AuthContext = Depends(_owner)) -> dict:
    _prune()
    flow = _FLOWS.get(state)
    if flow is not None and flow.org_id == auth.org_id:
        _FLOWS.pop(state, None)
    return {}

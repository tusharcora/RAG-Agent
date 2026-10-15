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
import logging
import secrets
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import connect_page
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


def _fail(flow: Flow, message: str) -> HTMLResponse:
    flow.status, flow.message = "error", message
    return connect_page.render("error", flow.purpose, title=message, message=message)


def _expired() -> HTMLResponse:
    return connect_page.render("error", None, title=broker.EXPIRED, message=broker.EXPIRED)


@router.get("/assets/{name}")
async def asset(name: str) -> FileResponse:
    """Fonts for the finish page (bundled, so the page works offline)."""
    path = connect_page.asset_path(name)
    if path is None:
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(
        path,
        media_type=connect_page.ASSET_TYPES[path.suffix],
        headers={"Cache-Control": "public, max-age=604800", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/finish")
async def finish(state: str = "", pickup: str = "", session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    _prune()
    flow = _FLOWS.get(state)
    # No pickup: not a redirect from the Worker's callback. Leave the flow alone (don't burn it) and say expired.
    if flow is None or flow.status != "pending" or flow.redeeming or not pickup:
        return _expired()

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
                headline = f"Connected to {name}"
                kind = "connected"
                card = connect_page.Card(result.get("workspace_name") or "Notion workspace", "Notion workspace")
            elif flow.purpose == "jira":
                site = {"id": result["cloud_id"], "url": result["site_url"], "name": result.get("site_name")}
                await save_jira_connection(session, flow.org_id, result, site)
                headline = f"Connected to {name}"
                kind = "connected"
                host = urlparse(result["site_url"]).netloc or result["site_url"]
                card = connect_page.Card(result.get("site_name") or host, host)
            else:
                handler = IDENTITY_HANDLER
                if handler is None or flow.user_id is None:
                    return _fail(flow, FAILED)
                try:
                    display = await handler(session, flow.user_id, result)
                except ValueError as exc:
                    return _fail(flow, str(exc) or FAILED)
                headline = f"Signed in as {display}"
                kind = "signed_in"
                email = result.get("email")
                card = connect_page.Card(display, email if email and email != display else None)
        except Exception as exc:  # noqa: BLE001 - the page must not 500; keep tokens out of the log
            logger.error("saving the %s sign-in failed: %s", flow.purpose, type(exc).__name__)
            await session.rollback()
            return _fail(flow, FAILED)

        flow.status, flow.message = "done", headline
        return connect_page.render(kind, flow.purpose, title=headline, card=card)
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

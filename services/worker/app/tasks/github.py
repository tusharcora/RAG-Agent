"""GitHub as a knowledge source, worker side (spec §5 "Worker").

The api's sync (app/integrations/github_sync.py) publishes one event per changed item:
  rag.github_file_updated   {connection_id, org_id, repo_id, full_name, path, sha, html_url}
  rag.github_issue_updated  {connection_id, org_id, repo_id, full_name, number, updated_at, html_url}
  rag.github_pr_updated     (same as issues)

Each handler fetches the item with `GithubClient`, assembles one text, and upserts it through
embed.py's `_sync_document`, writing `external_version` (the payload's blob `sha` / raw
`updated_at` string — exactly what the api compares against) in the same commit as the content.
All GitHub calls happen before the write session opens, so the token refresh's row lock is
never waited on by a session of ours.

- 404/410 (or a redirect out of the repository: an issue transferred elsewhere) on the item
  itself → its document and recorded failure are deleted and the event is acked.
- Disconnected or deleted connection → acked and skipped. Needs-reconnect and GitHub being
  unavailable raise, so the event retries and then dead-letters.
- `record_github_failure` is the dead-letter hook: it records the item's version in
  `github_failures`, so syncs stop re-enqueueing it until it changes on GitHub.
"""

import base64
import logging
import uuid
from collections.abc import Awaitable, Callable
from functools import partial

from sqlalchemy import delete, func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.db import get_session
from app.core.telemetry import tracer
from app.integrations import github
from app.models.rag import Document, GithubFailure, OAuthConnection
from app.tasks import embed

logger = logging.getLogger(__name__)

GONE_STATUSES = (404, 410)

# Dead-letter errors that are about the connection, not the item. Recording them would keep the
# item skipped until it changes on GitHub, even after Reconnect (or GitHub recovering) fixed it.
CONNECTION_ERRORS = frozenset(
    {github.RECONNECT, github.DISCONNECTED, github.UNAVAILABLE, github.API_UNAVAILABLE, github.TOKEN_REFUSED}
)
MAX_ERROR_LENGTH = 2000


class _Gone(Exception):
    pass


def _external_id(kind: str, payload: dict) -> str:
    if kind == "file":
        return f"file:{payload['repo_id']}:{payload['path']}"
    return f"{kind}:{payload['repo_id']}:{payload['number']}"


def _version(kind: str, payload: dict) -> str:
    return payload["sha"] if kind == "file" else payload["updated_at"]


async def _item(client: github.GithubClient, path: str) -> dict:
    """The item itself: _Gone on 404/410 or a redirect that left the repository."""
    resp = await client.get(path)
    if resp.status_code in GONE_STATUSES or resp.status_code in github.REDIRECTS:
        raise _Gone()
    if resp.status_code != 200:
        raise github.GithubStatusError(resp.status_code, path)
    return resp.json()


def _login(entry: dict) -> str:
    return ((entry.get("user") or {}).get("login")) or "ghost"


def _header(item: dict) -> str:
    """`# {title}\\nState: {state} · Labels: {a, b}\\n\\n{body}`."""
    labels = ", ".join(
        (label.get("name") if isinstance(label, dict) else str(label)) or "" for label in item.get("labels") or []
    )
    state = "merged" if item.get("merged_at") else (item.get("state") or "")
    head = f"# {item.get('title') or ''}\nState: {state} · Labels: {labels}".rstrip()
    body = (item.get("body") or "").strip()
    return f"{head}\n\n{body}" if body else head


def _comment_blocks(comments: list[dict]) -> list[str]:
    return [f"**{_login(c)}:** {c['body'].strip()}" for c in comments if (c.get("body") or "").strip()]


def _issue_text(issue: dict, comments: list[dict]) -> str:
    return "\n\n".join([_header(issue), *_comment_blocks(comments)])


def _pr_text(pr: dict, conversation: list[dict], reviews: list[dict], review_comments: list[dict],
             files: list[dict]) -> str:
    parts = [_header(pr)]
    if blocks := _comment_blocks(conversation):
        parts += ["## Conversation", *blocks]
    review_blocks = [
        f"**{_login(r)} ({(r.get('state') or 'commented').lower()}):** {r['body'].strip()}"
        for r in reviews
        if (r.get("body") or "").strip()
    ]
    if review_blocks:
        parts += ["## Reviews", *review_blocks]
    inline = [
        f"**{_login(c)}** on {c.get('path') or 'a file'}: {c['body'].strip()}"
        for c in review_comments
        if (c.get("body") or "").strip()
    ]
    if inline:
        parts += ["## Review comments", *inline]
    names = [f["filename"] for f in files if f.get("filename")]
    if names:
        parts += ["## Changed files", "\n".join(f"- {name}" for name in names)]
    return "\n\n".join(parts)


async def _store(kind: str, payload: dict, fetch: Callable[[github.GithubClient], Awaitable[tuple]]) -> None:
    """`fetch` → (title, text, last_edited_at), GitHub calls only; then one write session:
    upsert, or delete when the item is gone."""
    connection_id = uuid.UUID(payload["connection_id"])
    external_id = _external_id(kind, payload)
    gone = False
    try:
        async with github.GithubClient(connection_id, payload["repo_id"]) as client:
            try:
                title, text, last_edited_at = await fetch(client)
            except _Gone:
                gone = True
    except github.GithubDisconnected:
        logger.info("connection_id=%s is disconnected, skipping %s", connection_id, external_id)
        return

    async with get_session() as session:
        connection = await session.get(OAuthConnection, connection_id)
        if connection is None or embed._disconnected(connection):  # disconnected while fetching
            return
        await session.execute(
            delete(GithubFailure).where(
                GithubFailure.connection_id == connection_id, GithubFailure.external_id == external_id
            )
        )
        if gone:
            await session.execute(
                delete(Document).where(
                    Document.connection_id == connection_id,
                    Document.source == "github",
                    Document.external_id == external_id,
                )
            )
            await session.commit()
            logger.info("%s is gone from GitHub; removed its document", external_id)
            return
        # Commits the failure-row delete together with the upsert (or rolls both back).
        await embed._sync_document(
            session,
            source="github",
            external_id=external_id,
            connection_id=connection_id,
            title=title,
            url=payload.get("html_url") or "",
            last_edited_at=last_edited_at,
            sections=[(text, {})],
            external_version=_version(kind, payload),
        )


async def handle_github_file_updated(payload: dict) -> None:
    full_name, path, sha = payload["full_name"], payload["path"], payload["sha"]

    async def fetch(client):
        data = await _item(client, f"/repos/{full_name}/git/blobs/{sha}")
        raw = data.get("content") or ""
        content = base64.b64decode(raw) if data.get("encoding", "base64") == "base64" else raw.encode()
        return f"{full_name} · {path}", content.decode("utf-8", errors="replace"), None

    with tracer.start_as_current_span("embed.github_file", attributes={"repo": full_name, "path": path}):
        await _store("file", payload, fetch)


async def _issue_like(kind: str, payload: dict) -> None:
    full_name, number = payload["full_name"], payload["number"]

    async def fetch(client):
        base = f"/repos/{full_name}"
        if kind == "issue":
            item = await _item(client, f"{base}/issues/{number}")
            text = _issue_text(item, await client.all(f"{base}/issues/{number}/comments"))
        else:
            item = await _item(client, f"{base}/pulls/{number}")
            text = _pr_text(
                item,
                await client.all(f"{base}/issues/{number}/comments"),
                await client.all(f"{base}/pulls/{number}/reviews"),
                await client.all(f"{base}/pulls/{number}/comments"),
                await client.all(f"{base}/pulls/{number}/files"),
            )
        return f"{full_name} #{number}: {item.get('title') or ''}", text, item.get("updated_at")

    span = "embed.github_issue" if kind == "issue" else "embed.github_pr"
    with tracer.start_as_current_span(span, attributes={"repo": full_name, "number": number}):
        await _store(kind, payload, fetch)


async def handle_github_issue_updated(payload: dict) -> None:
    await _issue_like("issue", payload)


async def handle_github_pr_updated(payload: dict) -> None:
    await _issue_like("pr", payload)


async def record_github_failure(payload: dict, error: str, *, kind: str) -> None:
    """Dead-letter hook: upsert github_failures (version = payload sha/updated_at, attempts += 1).
    Errors about the connection rather than the item aren't recorded (see CONNECTION_ERRORS)."""
    if error in CONNECTION_ERRORS:
        logger.info("Not recording a GitHub failure caused by the connection, not the item")
        return
    connection_id = uuid.UUID(payload["connection_id"])
    external_id, version = _external_id(kind, payload), _version(kind, payload)
    stmt = pg_insert(GithubFailure).values(
        connection_id=connection_id,
        external_id=external_id,
        version=version,
        attempts=1,
        last_error=(error or "")[:MAX_ERROR_LENGTH],
        updated_at=func.now(),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[GithubFailure.connection_id, GithubFailure.external_id],
        set_={
            "version": stmt.excluded.version,
            "attempts": GithubFailure.attempts + 1,
            "last_error": stmt.excluded.last_error,
            "updated_at": func.now(),
        },
    )
    async with get_session() as session:
        await session.execute(stmt)
        await session.commit()
    logger.info("Recorded dead-lettered %s at version %s", external_id, version)


HANDLERS = {
    "rag.github_file_updated": handle_github_file_updated,
    "rag.github_issue_updated": handle_github_issue_updated,
    "rag.github_pr_updated": handle_github_pr_updated,
}

ON_DEAD_LETTER = {
    "rag.github_file_updated": partial(record_github_failure, kind="file"),
    "rag.github_issue_updated": partial(record_github_failure, kind="issue"),
    "rag.github_pr_updated": partial(record_github_failure, kind="pr"),
}

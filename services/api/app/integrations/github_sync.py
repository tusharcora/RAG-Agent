"""GitHub sync enumeration (spec §5): POST /sync/github and auto_sync.

A sync lists the repositories of every installation the user can reach, then enqueues at
most `max_github_items_per_sync` changed items for the worker. Each repo has two lazy
candidate streams, changed Markdown files and changed issues/PRs; the sync takes one item
per stream per pass, cycling through the repos from a starting repo that rotates every sync
(`oauth_connections.github_rr_offset`), so one large repo can't take the whole budget.

An item is "changed" when its version (blob sha / `updated_at`) differs from the stored
document's `external_version` and from a recorded `github_failures.version`; unchanged items
never count against the budget. Items enqueued but not yet processed still look changed to the
next sync and are enqueued again (the worker's dedupe makes that cheap), so this converges.

Nothing is deleted from a listing that didn't complete: repos only after every installation
listed in full, files only from an untruncated tree, issues/PRs only after a full scan paged
through the whole history window.

Token calls go through github_auth.github_access_token, which locks the connection row in
its own session: this module commits before every such call (see `refresh`) and only writes
the connection row itself (github_rr_offset) after the last GitHub call.
"""

import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from fastapi import HTTPException
from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.queue import publish_event
from app.integrations.connection_state import require_connected
from app.integrations.github_api import GitHubClient, GitHubError, GitHubStatusError, GitHubUnavailable
from app.integrations.github_auth import github_access_token
from app.models.rag import Document, GithubFailure, GithubRepo, OAuthConnection

logger = logging.getLogger(__name__)

MARKDOWN_SUFFIXES = (".md", ".mdx")
MAX_FILE_BYTES = 1024 * 1024
ISSUE_OVERLAP = timedelta(hours=1)
FULL_SCAN_EVERY = timedelta(days=7)
UNAVAILABLE = "Couldn't reach GitHub. Try again in a moment."


@dataclass
class _Item:
    routing_key: str
    external_id: str
    version: str
    payload: dict


def _parse_time(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None
    except ValueError:
        return None


def _since_param(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _repo_prefixes(repo_id: int) -> tuple[str, ...]:
    return (f"file:{repo_id}:", f"issue:{repo_id}:", f"pr:{repo_id}:")


class _Sync:
    """State shared by one sync's streams."""

    def __init__(self, session: AsyncSession, connection: OAuthConnection, client: GitHubClient):
        self.session = session
        self.connection_id = connection.id
        self.org_id = connection.org_id
        self.client = client
        self.now = datetime.now(timezone.utc)
        self.removed = 0

    def payload(self, repo: GithubRepo, **fields) -> dict:
        return {
            "connection_id": str(self.connection_id),
            "org_id": str(self.org_id),
            "repo_id": repo.repo_id,
            "full_name": repo.full_name,
            **fields,
        }

    def _matching(self, column, prefixes: tuple[str, ...]):
        return or_(*(column.startswith(prefix, autoescape=True) for prefix in prefixes))

    async def versions(self, *prefixes: str) -> tuple[dict[str, str | None], dict[str, str]]:
        """{external_id: external_version} of stored documents and {external_id: failed version}."""
        docs = await self.session.execute(
            select(Document.external_id, Document.external_version).where(
                Document.connection_id == self.connection_id,
                Document.source == "github",
                self._matching(Document.external_id, prefixes),
            )
        )
        failures = await self.session.execute(
            select(GithubFailure.external_id, GithubFailure.version).where(
                GithubFailure.connection_id == self.connection_id,
                self._matching(GithubFailure.external_id, prefixes),
            )
        )
        return dict(docs.all()), dict(failures.all())

    async def delete_items(self, external_ids: list[str] | None = None, prefixes: tuple[str, ...] = ()) -> None:
        """Deletes documents (chunks cascade) and recorded failures by id or by prefix."""
        if external_ids is not None and not external_ids:
            return
        doc_match = Document.external_id.in_(external_ids) if external_ids is not None else self._matching(
            Document.external_id, prefixes
        )
        fail_match = GithubFailure.external_id.in_(external_ids) if external_ids is not None else self._matching(
            GithubFailure.external_id, prefixes
        )
        result = await self.session.execute(
            delete(Document)
            .where(Document.connection_id == self.connection_id, Document.source == "github", doc_match)
            .returning(Document.id)
        )
        self.removed += len(result.all())
        await self.session.execute(
            delete(GithubFailure).where(GithubFailure.connection_id == self.connection_id, fail_match)
        )


def _unchanged(external_id: str, version: str, docs: dict, failures: dict) -> bool:
    return docs.get(external_id) == version or failures.get(external_id) == version


class _FileStream:
    """Changed Markdown blobs of the default branch's tree. One tree call per sync; the tree sha
    is stored only after every changed file was enqueued, so an unchanged tree is skipped."""

    def __init__(self, ctx: _Sync, repo: GithubRepo):
        self.ctx, self.repo = ctx, repo
        self.pending: deque[_Item] | None = None
        self.tree_sha: str | None = None
        self.done = False

    async def next(self) -> _Item | None:
        if self.done:
            return None
        if self.pending is None:
            await self._load()
        if self.pending:
            return self.pending.popleft()
        # Asked again with nothing left: every changed file of this tree has been enqueued.
        if self.tree_sha:
            self.repo.tree_sha = self.tree_sha
        self.done = True
        return None

    async def _load(self) -> None:
        repo = self.repo
        self.pending = deque()
        if not repo.default_branch:
            return
        resp = await self.ctx.client.get(
            f"/repos/{repo.full_name}/git/trees/{quote(repo.default_branch, safe='')}", {"recursive": "1"}
        )
        if resp.status_code == 409:  # empty repository: nothing to do
            return
        if resp.status_code != 200:
            raise GitHubStatusError(resp.status_code, f"/repos/{repo.full_name}/git/trees")
        tree = resp.json()
        if tree.get("sha") and tree["sha"] == repo.tree_sha:
            return
        self.tree_sha = tree.get("sha")
        blobs = [
            entry
            for entry in tree.get("tree", [])
            if entry.get("type") == "blob"
            and entry.get("path", "").lower().endswith(MARKDOWN_SUFFIXES)
            and (entry.get("size") or 0) <= MAX_FILE_BYTES
        ]
        prefix = f"file:{repo.repo_id}:"
        docs, failures = await self.ctx.versions(prefix)
        if not tree.get("truncated"):
            # Gone from the tree, or no longer a syncable Markdown file (renamed, grew past 1 MB).
            keep = {prefix + entry["path"] for entry in blobs}
            await self.ctx.delete_items([eid for eid in docs if eid not in keep])
        for entry in blobs:
            external_id = prefix + entry["path"]
            if _unchanged(external_id, entry["sha"], docs, failures):
                continue
            path = entry["path"]
            html_url = f"{repo.html_url}/blob/{quote(repo.default_branch, safe='')}/{quote(path)}"
            self.pending.append(
                _Item(
                    "rag.github_file_updated",
                    external_id,
                    entry["sha"],
                    self.ctx.payload(repo, path=path, sha=entry["sha"], html_url=html_url),
                )
            )


class _IssueStream:
    """Changed issues and PRs, oldest update first, paged lazily. A normal sync starts at the
    stored cursor minus an hour of overlap; a full scan (weekly, or until one completes) starts at
    the history window and, once it paged through all of it, prunes what GitHub no longer returns."""

    def __init__(self, ctx: _Sync, repo: GithubRepo):
        self.ctx, self.repo = ctx, repo
        self.pages = None
        self.buffer: deque[dict] = deque()
        self.seen: set[str] = set()
        self.done = False

    async def next(self) -> _Item | None:
        if self.done:
            return None
        if self.pages is None:
            await self._load()
        while True:
            while self.buffer:
                item = self._examine(self.buffer.popleft())
                if item is not None:
                    return item
            page = await anext(self.pages, None)
            if page is None:
                await self._finish()
                return None
            self.buffer.extend(page)

    async def _load(self) -> None:
        repo, now = self.repo, self.ctx.now
        self.window_start = now - timedelta(days=settings.github_history_days)
        self.full_scan = repo.last_full_scan_at is None or repo.last_full_scan_at < now - FULL_SCAN_EVERY
        if self.full_scan or repo.issues_cursor is None:
            since = self.window_start
        else:
            since = repo.issues_cursor - ISSUE_OVERLAP
        self.docs, self.failures = await self.ctx.versions(f"issue:{repo.repo_id}:", f"pr:{repo.repo_id}:")
        self.pages = self.ctx.client.pages(
            f"/repos/{repo.full_name}/issues",
            {"state": "all", "sort": "updated", "direction": "asc", "since": _since_param(since)},
        )

    def _examine(self, raw: dict) -> _Item | None:
        repo = self.repo
        is_pr = bool(raw.get("pull_request"))
        external_id = f"{'pr' if is_pr else 'issue'}:{repo.repo_id}:{raw['number']}"
        version = raw["updated_at"]
        self.seen.add(external_id)
        updated = _parse_time(version)
        # Examined (enqueued now, or unchanged): the cursor may move past it.
        if updated and (repo.issues_cursor is None or updated > repo.issues_cursor):
            repo.issues_cursor = updated
        if _unchanged(external_id, version, self.docs, self.failures):
            return None
        return _Item(
            "rag.github_pr_updated" if is_pr else "rag.github_issue_updated",
            external_id,
            version,
            self.ctx.payload(repo, number=raw["number"], updated_at=version, html_url=raw.get("html_url", "")),
        )

    async def _finish(self) -> None:
        self.done = True
        if not self.full_scan:
            return
        # Only documents last updated inside the window: older ones were never asked for.
        gone = []
        for external_id, version in self.docs.items():
            updated = _parse_time(version)
            if external_id not in self.seen and updated is not None and updated >= self.window_start:
                gone.append(external_id)
        await self.ctx.delete_items(gone)
        self.repo.last_full_scan_at = self.ctx.now

    async def aclose(self) -> None:
        if self.pages is not None:
            await self.pages.aclose()


async def _list_repositories(client: GitHubClient) -> dict[int, tuple[int, dict]]:
    """{repo_id: (installation_id, repo)} across every installation; raises unless all listings completed."""
    installations: list[dict] = []
    async for page in client.pages("/user/installations", key="installations"):
        installations.extend(page)
    listed: dict[int, tuple[int, dict]] = {}
    for installation in installations:
        installation_id = int(installation["id"])
        async for page in client.pages(f"/user/installations/{installation_id}/repositories", key="repositories"):
            for repo in page:
                listed.setdefault(int(repo["id"]), (installation_id, repo))
    return listed


async def _save_repositories(ctx: _Sync, listed: dict[int, tuple[int, dict]]) -> None:
    """Upserts the listed repos (keeping their cursors) and removes the rest with their documents."""
    session = ctx.session
    for repo_id, (installation_id, repo) in listed.items():
        values = {
            "installation_id": installation_id,
            "full_name": repo["full_name"],
            "html_url": repo["html_url"],
            "default_branch": repo.get("default_branch") or "",
            "private": bool(repo.get("private")),
        }
        await session.execute(
            insert(GithubRepo)
            .values(connection_id=ctx.connection_id, repo_id=repo_id, **values)
            .on_conflict_do_update(index_elements=["connection_id", "repo_id"], set_=values)
        )
    stale = (
        await session.execute(
            select(GithubRepo.repo_id).where(
                GithubRepo.connection_id == ctx.connection_id, GithubRepo.repo_id.not_in(list(listed) or [-1])
            )
        )
    ).scalars().all()
    for repo_id in stale:
        await ctx.delete_items(prefixes=_repo_prefixes(repo_id))
    if stale:
        await session.execute(
            delete(GithubRepo).where(GithubRepo.connection_id == ctx.connection_id, GithubRepo.repo_id.in_(stale))
        )
        logger.info("GitHub sync: %d repositories no longer accessible for connection=%s", len(stale), ctx.connection_id)


async def sync_github_connection(session: AsyncSession, connection: OAuthConnection) -> dict:
    """Lists repositories and enqueues changed files, issues and PRs (see the module docstring).
    Shared by POST /sync/github and auto_sync. Raises HTTPException 409 (disconnected / needs
    reconnecting) or 502 (GitHub unreachable while listing repositories); once items are being
    enqueued, a rate limit or outage just stops the sync early (truncated) to resume next run."""
    require_connected(connection, "github")
    connection_id = connection.id
    token = await github_access_token(connection_id)

    async def refresh(failed_token: str) -> str:
        # github_access_token locks the connection row in its own session: nothing of ours may hold it.
        await session.commit()
        return await github_access_token(connection_id, failed_token=failed_token)

    async with GitHubClient(token, refresh) as client:
        ctx = _Sync(session, connection, client)
        try:
            listed = await _list_repositories(client)
        except GitHubError as exc:
            logger.warning("GitHub sync: repository listing failed for connection=%s: %s", connection_id, exc)
            raise HTTPException(status_code=502, detail=UNAVAILABLE) from exc
        await _save_repositories(ctx, listed)
        await session.commit()

        repos = (
            await session.execute(
                select(GithubRepo).where(GithubRepo.connection_id == connection_id).order_by(GithubRepo.repo_id)
            )
        ).scalars().all()
        published, incomplete = 0, False
        active: list[_FileStream | _IssueStream] = []
        if repos:
            start = (connection.github_rr_offset or 0) % len(repos)
            ordered = list(repos[start:]) + list(repos[:start])
            active = [stream for repo in ordered for stream in (_FileStream(ctx, repo), _IssueStream(ctx, repo))]
        streams = list(active)
        cap = settings.max_github_items_per_sync
        try:
            while active and published < cap:
                for stream in list(active):
                    if published >= cap:
                        break
                    try:
                        item = await stream.next()
                    except GitHubStatusError as exc:
                        # This repo only (e.g. removed since the listing); the next sync tries again.
                        logger.warning("GitHub sync: %s (connection=%s)", exc, connection_id)
                        active.remove(stream)
                        incomplete = True
                        continue
                    if item is None:
                        active.remove(stream)
                        continue
                    await publish_event(
                        session,
                        routing_key=item.routing_key,
                        payload=item.payload,
                        dedupe_key=f"github:{connection_id}:{item.external_id}:{item.version}",
                        org_id=connection.org_id,
                    )
                    published += 1
        except GitHubUnavailable as exc:
            logger.warning("GitHub sync stopped early for connection=%s: %s", connection_id, exc)
            incomplete = True
        finally:
            for stream in streams:
                if isinstance(stream, _IssueStream):
                    await stream.aclose()

    truncated = incomplete or bool(active)
    # Last, after every GitHub (and so token) call: this write locks the connection row until the commit.
    await session.execute(
        update(OAuthConnection)
        .where(OAuthConnection.id == connection_id)
        .values(github_rr_offset=OAuthConnection.github_rr_offset + 1)
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    logger.info(
        "GitHub sync connection=%s repos=%d published=%d truncated=%s removed=%d",
        connection_id, len(repos), published, truncated, ctx.removed,
    )
    return {"published": published, "truncated": truncated, "removed": ctx.removed}

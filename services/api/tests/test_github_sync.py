"""GitHub sync enumeration (app/integrations/github_sync.py, POST /sync/github, auto_sync):
repo listing across installations, budgeted round-robin over per-repo file and issue
streams, version skipping, tree/issue cursors, full scans and all-or-nothing pruning.

GitHub is faked with httpx.MockTransport through github_api.TRANSPORT."""

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.routes import connections as connections_routes
from app.api.routes import sync as sync_routes
from app.core import auto_sync, db, queue, ratelimit
from app.core.auth import AuthContext, require_auth_or_service_token
from app.core.config import settings
from app.core.crypto import encrypt_token
from app.integrations import github_api, github_auth, github_sync
from app.models.rag import OAuthConnection

from .conftest import db_run

API = "https://gh.test"
RECONNECT = "GitHub needs reconnecting. Use Reconnect on the Connections page."
DISCONNECTED = "GitHub is disconnected. Connect it again to sync."


# --------------------------------------------------------------------------- fake GitHub


def repo(rid, name, branch="main"):
    return {
        "id": rid,
        "full_name": f"octo/{name}",
        "html_url": f"https://github.com/octo/{name}",
        "default_branch": branch,
        "private": True,
    }


def blob(path, sha, size=100):
    return {"path": path, "type": "blob", "sha": sha, "size": size}


def issue(n, updated, pr=False, name="r1"):
    item = {
        "number": n,
        "title": f"Item {n}",
        "updated_at": updated,
        "html_url": f"https://github.com/octo/{name}/{'pull' if pr else 'issues'}/{n}",
    }
    if pr:
        item["pull_request"] = {"url": "x"}
    return item


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeGitHub:
    def __init__(self):
        self.installations: dict[int, list[dict]] = {}
        self.trees: dict[str, dict | int] = {}
        self.issues: dict[str, list[dict]] = {}
        # Items GET /repos/{full_name}/issues/{n} still finds although the listing skipped them.
        self.hidden: dict[tuple[str, int], dict] = {}
        self.fail: dict[tuple[str, int], int] = {}  # (path, page) -> status, every time
        self.requests: list[httpx.Request] = []
        self.per_page = 2
        self.tokens = {"tok"}

    def _page(self, request, items, wrap=None):
        page = int(request.url.params.get("page", "1"))
        chunk = items[(page - 1) * self.per_page : page * self.per_page]
        headers = {}
        if page * self.per_page < len(items):
            params = dict(request.url.params)
            params["page"] = str(page + 1)
            headers["Link"] = f'<{API}{request.url.path}?{urlencode(params)}>; rel="next"'
        body = {"total_count": len(items), wrap: chunk} if wrap else chunk
        return httpx.Response(200, json=body, headers=headers)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        auth = request.headers.get("Authorization", "")
        if auth.removeprefix("Bearer ") not in self.tokens:
            return httpx.Response(401, json={"message": "Bad credentials"})
        path = request.url.path
        page = int(request.url.params.get("page", "1"))
        if (path, page) in self.fail:
            return httpx.Response(self.fail[(path, page)], json={"message": "boom"})
        parts = path.strip("/").split("/")
        if path == "/user/installations":
            items = [{"id": i, "account": {"login": "octo"}} for i in self.installations]
            return self._page(request, items, "installations")
        if parts[:2] == ["user", "installations"] and parts[3:] == ["repositories"]:
            return self._page(request, self.installations[int(parts[2])], "repositories")
        if parts[0] == "repos" and parts[3:5] == ["git", "trees"]:
            tree = self.trees.get(f"{parts[1]}/{parts[2]}", {"sha": "empty-tree", "tree": [], "truncated": False})
            if isinstance(tree, int):
                return httpx.Response(tree, json={"message": "Git Repository is empty."})
            return httpx.Response(200, json=tree)
        if parts[0] == "repos" and len(parts) == 5 and parts[3] == "issues":
            full_name, number = f"{parts[1]}/{parts[2]}", int(parts[4])
            found = [i for i in self.issues.get(full_name, []) if i["number"] == number]
            raw = found[0] if found else self.hidden.get((full_name, number))
            if raw is None:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json=raw)
        if parts[0] == "repos" and parts[3:] == ["issues"]:
            since = request.url.params.get("since")
            items = sorted(self.issues.get(f"{parts[1]}/{parts[2]}", []), key=lambda i: i["updated_at"])
            if since:
                items = [i for i in items if i["updated_at"] >= since]
            return self._page(request, items)
        return httpx.Response(404, json={"message": "Not Found"})

    def calls(self, contains):
        return [r for r in self.requests if contains in r.url.path]


# --------------------------------------------------------------------------- fixtures


@pytest.fixture
def factory(pg_dsn, monkeypatch):
    """app.core.db's session factory (what github_access_token opens) on the test database.
    NullPool: tests drive it from several event loops."""
    monkeypatch.setattr(settings, "token_encryption_key", Fernet.generate_key().decode())
    engine = create_async_engine(pg_dsn.replace("postgresql://", "postgresql+asyncpg://", 1), poolclass=NullPool)
    made = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(db, "_session_factory", made)
    return made


@pytest.fixture
def gh(monkeypatch, factory):
    fake = FakeGitHub()
    monkeypatch.setattr(github_api, "TRANSPORT", httpx.MockTransport(fake.handler))
    monkeypatch.setattr(settings, "github_api_url", API)
    monkeypatch.setattr(settings, "queue_backend", "postgres")
    monkeypatch.setattr(settings, "max_github_items_per_sync", 500)
    monkeypatch.setattr(settings, "github_history_days", 365)

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(github_api, "_sleep", no_sleep)
    return fake


@pytest.fixture
def published(monkeypatch):
    """Every event the sync publishes, in order (the real publish_event still runs)."""
    events: list[dict] = []
    real = github_sync.publish_event

    async def record(session, routing_key, payload, dedupe_key, org_id=None):
        events.append({"routing_key": routing_key, "payload": payload, "dedupe_key": dedupe_key})
        return await real(session, routing_key, payload, dedupe_key, org_id)

    monkeypatch.setattr(github_sync, "publish_event", record)
    return events


def seed_conn(dsn, org_id=None, token="tok", expires_in=timedelta(hours=1), rr_offset=0):
    org_id = org_id or uuid.uuid4()
    if not db_run(dsn, "SELECT 1 FROM organizations WHERE id = $1", org_id):
        db_run(dsn, "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T', $2)", org_id, f"t-{org_id}")
    conn_id = uuid.uuid4()
    db_run(
        dsn,
        "INSERT INTO oauth_connections (id, org_id, provider, workspace_id, workspace_name, access_token,"
        " refresh_token, expires_at, github_rr_offset) VALUES ($1, $2, 'github', '42', 'octo', $3, $4, $5, $6)",
        conn_id,
        org_id,
        encrypt_token(token),
        encrypt_token("refresh"),
        datetime.now(timezone.utc) + expires_in,
        rr_offset,
    )
    return org_id, conn_id


def seed_repo(dsn, conn_id, r, installation=1, tree_sha=None, cursor=None, full_scan=None):
    db_run(
        dsn,
        "INSERT INTO github_repos (connection_id, installation_id, repo_id, full_name, html_url, default_branch,"
        " private, tree_sha, issues_cursor, last_full_scan_at) VALUES ($1, $2, $3, $4, $5, $6, true, $7, $8, $9)",
        conn_id,
        installation,
        r["id"],
        r["full_name"],
        r["html_url"],
        r["default_branch"],
        tree_sha,
        cursor,
        full_scan,
    )


def seed_doc(dsn, conn_id, external_id, version):
    db_run(
        dsn,
        "INSERT INTO documents (id, source, external_id, connection_id, title, url, external_version)"
        " VALUES ($1, 'github', $2, $3, $2, 'https://x', $4)"
        " ON CONFLICT (connection_id, source, external_id) DO UPDATE SET external_version = EXCLUDED.external_version",
        uuid.uuid4(),
        external_id,
        conn_id,
        version,
    )


def external_id(event):
    p = event["payload"]
    if event["routing_key"] == "rag.github_file_updated":
        return f"file:{p['repo_id']}:{p['path']}", p["sha"]
    kind = "pr" if event["routing_key"] == "rag.github_pr_updated" else "issue"
    return f"{kind}:{p['repo_id']}:{p['number']}", p["updated_at"]


def fake_worker(dsn, conn_id, events):
    """What Task 6's worker does on success: store the document with its external_version."""
    import asyncpg

    rows = [(uuid.uuid4(), *external_id(event), conn_id) for event in events]

    async def go():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.executemany(
                "INSERT INTO documents (id, source, external_id, connection_id, title, url, external_version)"
                " VALUES ($1, 'github', $2, $4, $2, 'https://x', $3) ON CONFLICT (connection_id, source, external_id)"
                " DO UPDATE SET external_version = EXCLUDED.external_version",
                rows,
            )
        finally:
            await conn.close()

    asyncio.run(go())


def docs(dsn, conn_id):
    rows = db_run(dsn, "SELECT external_id, external_version FROM documents WHERE connection_id = $1", conn_id)
    return {r["external_id"]: r["external_version"] for r in rows}


def repos(dsn, conn_id):
    rows = db_run(dsn, "SELECT * FROM github_repos WHERE connection_id = $1 ORDER BY repo_id", conn_id)
    return {r["repo_id"]: r for r in rows}


def sync(factory, conn_id):
    async def go():
        async with factory() as session:
            connection = await session.get(OAuthConnection, conn_id)
            return await github_sync.sync_github_connection(session, connection)

    return asyncio.run(go())


def paths(events):
    return [e["payload"].get("path") or e["payload"].get("number") for e in events]


# --------------------------------------------------------------------------- repositories


def test_sync_lists_repos_across_installations_and_upserts(gh, published, factory, pg_dsn):
    _, conn_id = seed_conn(pg_dsn)
    seed_repo(pg_dsn, conn_id, repo(11, "old-name"), installation=1, tree_sha="t", cursor=datetime(2026, 1, 1, tzinfo=timezone.utc))
    gh.installations = {1: [repo(11, "a"), repo(12, "b"), repo(13, "c")], 2: [repo(21, "d", branch="trunk")]}
    result = sync(factory, conn_id)
    assert result == {"published": 0, "truncated": False, "removed": 0}
    rows = repos(pg_dsn, conn_id)
    assert set(rows) == {11, 12, 13, 21}
    assert rows[11]["full_name"] == "octo/a" and rows[11]["html_url"] == "https://github.com/octo/a"
    assert rows[11]["issues_cursor"] == datetime(2026, 1, 1, tzinfo=timezone.utc)  # cursors survive the upsert
    assert rows[21]["installation_id"] == 2 and rows[21]["default_branch"] == "trunk"
    assert len(gh.calls("/user/installations/1/repositories")) == 2  # paginated
    assert len(gh.calls("/repos/octo/d/git/trees/trunk")) == 1


def test_partial_repo_listing_deletes_nothing(gh, published, factory, pg_dsn):
    _, conn_id = seed_conn(pg_dsn)
    seed_repo(pg_dsn, conn_id, repo(99, "x"))
    seed_doc(pg_dsn, conn_id, "file:99:README.md", "s1")
    gh.installations = {1: [repo(11, "a"), repo(12, "b"), repo(13, "c")]}
    gh.fail[("/user/installations/1/repositories", 2)] = 500
    with pytest.raises(HTTPException) as err:
        sync(factory, conn_id)
    assert err.value.status_code == 502
    assert set(repos(pg_dsn, conn_id)) == {99}
    assert docs(pg_dsn, conn_id) == {"file:99:README.md": "s1"}
    assert len(gh.calls("/user/installations/1/repositories")) > 2  # page 2 was retried


def test_removed_repo_deletes_its_documents(gh, published, factory, pg_dsn):
    _, conn_id = seed_conn(pg_dsn)
    seed_repo(pg_dsn, conn_id, repo(99, "x"))
    seed_repo(pg_dsn, conn_id, repo(11, "a"), tree_sha="empty-tree")
    for eid in ("file:99:README.md", "issue:99:1", "pr:99:2", "file:11:README.md"):
        seed_doc(pg_dsn, conn_id, eid, "v")
    db_run(pg_dsn, "INSERT INTO github_failures (connection_id, external_id, version) VALUES ($1, 'issue:99:3', 'v')", conn_id)
    gh.installations = {1: [repo(11, "a")]}
    gh.trees["octo/a"] = {"sha": "empty-tree", "tree": [blob("README.md", "v")], "truncated": False}
    result = sync(factory, conn_id)
    assert result["removed"] == 3
    assert set(repos(pg_dsn, conn_id)) == {11}
    assert docs(pg_dsn, conn_id) == {"file:11:README.md": "v"}
    assert db_run(pg_dsn, "SELECT 1 FROM github_failures WHERE connection_id = $1", conn_id) == []


def test_sync_with_no_installations(gh, published, factory, pg_dsn):
    _, conn_id = seed_conn(pg_dsn)
    assert sync(factory, conn_id) == {"published": 0, "truncated": False, "removed": 0}
    assert published == []


# --------------------------------------------------------------------------- files


def test_files_filter_md_mdx_and_size(gh, published, factory, pg_dsn):
    _, conn_id = seed_conn(pg_dsn)
    gh.installations = {1: [repo(11, "a")]}
    gh.trees["octo/a"] = {
        "sha": "T1",
        "truncated": False,
        "tree": [
            blob("docs/a.md", "s1"),
            blob("README.md", "s2"),
            blob("x.mdx", "s3"),
            blob("logo.png", "s4"),
            blob("big.md", "s5", size=2 * 1024 * 1024),
            {"path": "docs", "type": "tree", "sha": "d"},
            blob("notes.txt", "s6"),
        ],
    }
    result = sync(factory, conn_id)
    assert result == {"published": 3, "truncated": False, "removed": 0}
    assert sorted(paths(published)) == ["README.md", "docs/a.md", "x.mdx"]
    event = next(e for e in published if e["payload"]["path"] == "docs/a.md")
    assert event["routing_key"] == "rag.github_file_updated"
    row_id = repos(pg_dsn, conn_id)[11]["id"]
    assert event["dedupe_key"] == f"github:{conn_id}:{row_id}:file:11:docs/a.md:s1"
    assert set(event["payload"]) == {"connection_id", "org_id", "repo_id", "full_name", "path", "sha", "html_url"}
    assert event["payload"]["html_url"] == "https://github.com/octo/a/blob/main/docs/a.md"
    assert event["payload"]["repo_id"] == 11 and event["payload"]["full_name"] == "octo/a"
    assert repos(pg_dsn, conn_id)[11]["tree_sha"] == "T1"  # every changed file enqueued


def test_unchanged_blob_not_enqueued(gh, published, factory, pg_dsn):
    _, conn_id = seed_conn(pg_dsn)
    gh.installations = {1: [repo(11, "a")]}
    gh.trees["octo/a"] = {"sha": "T1", "truncated": False, "tree": [blob("a.md", "s1"), blob("b.md", "s2")]}
    seed_doc(pg_dsn, conn_id, "file:11:a.md", "s1")
    seed_doc(pg_dsn, conn_id, "file:11:b.md", "old")
    assert sync(factory, conn_id)["published"] == 1
    assert paths(published) == ["b.md"]


def test_truncated_tree_deletes_nothing(gh, published, factory, pg_dsn):
    _, conn_id = seed_conn(pg_dsn)
    gh.installations = {1: [repo(11, "a")]}
    gh.trees["octo/a"] = {"sha": "T1", "truncated": True, "tree": [blob("a.md", "s1")]}
    seed_doc(pg_dsn, conn_id, "file:11:a.md", "s1")
    seed_doc(pg_dsn, conn_id, "file:11:gone.md", "s9")
    assert sync(factory, conn_id)["removed"] == 0
    assert set(docs(pg_dsn, conn_id)) == {"file:11:a.md", "file:11:gone.md"}


def test_complete_tree_deletes_removed_paths(gh, published, factory, pg_dsn):
    _, conn_id = seed_conn(pg_dsn)
    gh.installations = {1: [repo(11, "a"), repo(12, "b")]}
    gh.trees["octo/a"] = {"sha": "T1", "truncated": False, "tree": [blob("a.md", "s1")]}
    seed_doc(pg_dsn, conn_id, "file:11:a.md", "s1")
    seed_doc(pg_dsn, conn_id, "file:11:gone.md", "s9")
    seed_doc(pg_dsn, conn_id, "file:12:gone.md", "s9")  # another repo's file: its tree is not this one
    seed_doc(pg_dsn, conn_id, "issue:11:1", "2024-01-01T00:00:00Z")  # not a file (and outside the issue window)
    gh.trees["octo/b"] = {"sha": "T2", "truncated": False, "tree": [blob("gone.md", "s9")]}
    assert sync(factory, conn_id)["removed"] == 1
    assert set(docs(pg_dsn, conn_id)) == {"file:11:a.md", "file:12:gone.md", "issue:11:1"}


def test_empty_repo_409_is_skipped(gh, published, factory, pg_dsn):
    _, conn_id = seed_conn(pg_dsn)
    gh.installations = {1: [repo(11, "empty"), repo(12, "b")]}
    gh.trees["octo/empty"] = 409
    gh.trees["octo/b"] = {"sha": "T2", "truncated": False, "tree": [blob("a.md", "s1")]}
    result = sync(factory, conn_id)
    assert result == {"published": 1, "truncated": False, "removed": 0}
    assert published[0]["payload"]["full_name"] == "octo/b"


def test_unchanged_tree_sha_skips_listing(gh, published, factory, pg_dsn):
    _, conn_id = seed_conn(pg_dsn)
    seed_repo(pg_dsn, conn_id, repo(11, "a"), tree_sha="T1")
    gh.installations = {1: [repo(11, "a")]}
    # No documents at all, yet nothing is enqueued: the tree hasn't moved since every file was enqueued.
    gh.trees["octo/a"] = {"sha": "T1", "truncated": False, "tree": [blob("a.md", "s1")]}
    assert sync(factory, conn_id)["published"] == 0


def test_budget_converges_large_repo(gh, published, factory, pg_dsn, monkeypatch):
    monkeypatch.setattr(settings, "max_github_items_per_sync", 50)
    _, conn_id = seed_conn(pg_dsn)
    gh.installations = {1: [repo(11, "big")]}
    gh.trees["octo/big"] = {
        "sha": "T1",
        "truncated": False,
        "tree": [blob(f"docs/{i:04}.md", f"s{i}") for i in range(120)],
    }
    counts = []
    for _ in range(4):
        before = len(published)
        result = sync(factory, conn_id)
        batch = published[before:]
        counts.append((result["published"], result["truncated"]))
        assert len(batch) == result["published"]
        fake_worker(pg_dsn, conn_id, batch)
    assert counts == [(50, True), (50, True), (20, False), (0, False)]
    assert len({external_id(e)[0] for e in published}) == 120
    assert repos(pg_dsn, conn_id)[11]["tree_sha"] == "T1"


def test_round_robin_rotates(gh, published, factory, pg_dsn, monkeypatch):
    monkeypatch.setattr(settings, "max_github_items_per_sync", 4)
    _, conn_id = seed_conn(pg_dsn)
    gh.installations = {1: [repo(11, "a"), repo(12, "b"), repo(13, "c")]}
    for name in "abc":
        gh.trees[f"octo/{name}"] = {
            "sha": f"T{name}",
            "truncated": False,
            "tree": [blob(f"{name}{i}.md", f"{name}{i}") for i in range(10)],
        }
    result = sync(factory, conn_id)
    assert result["truncated"] is True
    assert paths(published) == ["a0.md", "b0.md", "c0.md", "a1.md"]  # one per stream per pass, every repo served

    first = len(published)
    sync(factory, conn_id)  # nothing was processed, so the same files are still changed
    assert paths(published[first:]) == ["b0.md", "c0.md", "a0.md", "b1.md"]  # starts at repo 2
    (row,) = db_run(pg_dsn, "SELECT github_rr_offset FROM oauth_connections WHERE id = $1", conn_id)
    assert row["github_rr_offset"] == 2


# --------------------------------------------------------------------------- issues and PRs


def test_issue_cursor_overlap_and_routing(gh, published, factory, pg_dsn):
    _, conn_id = seed_conn(pg_dsn)
    cursor = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
    seed_repo(pg_dsn, conn_id, repo(11, "a"), tree_sha="empty-tree", cursor=cursor,
              full_scan=datetime.now(timezone.utc) - timedelta(days=1))
    gh.installations = {1: [repo(11, "a")]}
    gh.issues["octo/a"] = [
        issue(1, "2026-10-01T11:30:00Z"),  # inside the 1 h overlap: examined again
        issue(2, "2026-10-02T09:00:00Z", pr=True),
        issue(3, "2026-10-03T09:00:00Z"),
    ]
    seed_doc(pg_dsn, conn_id, "issue:11:1", "2026-10-01T11:30:00Z")  # unchanged
    result = sync(factory, conn_id)
    assert result["published"] == 2
    call = gh.calls("/repos/octo/a/issues")[0]
    params = call.url.params
    assert params["since"] == "2026-10-01T11:00:00Z"
    assert (params["state"], params["sort"], params["direction"]) == ("all", "updated", "asc")
    by_number = {e["payload"]["number"]: e for e in published}
    assert by_number[2]["routing_key"] == "rag.github_pr_updated"
    assert by_number[3]["routing_key"] == "rag.github_issue_updated"
    row_id = repos(pg_dsn, conn_id)[11]["id"]
    assert by_number[2]["dedupe_key"] == f"github:{conn_id}:{row_id}:pr:11:2:2026-10-02T09:00:00Z"
    assert set(by_number[3]["payload"]) == {
        "connection_id", "org_id", "repo_id", "full_name", "number", "updated_at", "html_url"
    }
    assert by_number[3]["payload"]["html_url"] == "https://github.com/octo/r1/issues/3"
    assert repos(pg_dsn, conn_id)[11]["issues_cursor"] == datetime(2026, 10, 3, 9, 0, tzinfo=timezone.utc)


def test_first_sync_uses_history_window(gh, published, factory, pg_dsn):
    _, conn_id = seed_conn(pg_dsn)
    gh.installations = {1: [repo(11, "a")]}
    sync(factory, conn_id)
    (call,) = gh.calls("/repos/octo/a/issues")
    since = datetime.strptime(call.url.params["since"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert abs(since - (datetime.now(timezone.utc) - timedelta(days=365))) < timedelta(minutes=5)
    assert repos(pg_dsn, conn_id)[11]["last_full_scan_at"] is not None


def test_full_scan_recovers_missing_version_and_prunes_deleted(gh, published, factory, pg_dsn):
    _, conn_id = seed_conn(pg_dsn)
    now = datetime.now(timezone.utc)
    seed_repo(pg_dsn, conn_id, repo(11, "a"), tree_sha="empty-tree", cursor=now - timedelta(hours=2),
              full_scan=now - timedelta(days=8))
    gh.installations = {1: [repo(11, "a")]}
    old, older = iso(now - timedelta(days=30)), iso(now - timedelta(days=20))
    gh.issues["octo/a"] = [issue(1, old), issue(2, older, pr=True)]
    seed_doc(pg_dsn, conn_id, "issue:11:1", old)  # unchanged
    seed_doc(pg_dsn, conn_id, "pr:11:2", iso(now - timedelta(days=25)))  # lost an update: recovered
    seed_doc(pg_dsn, conn_id, "issue:11:3", iso(now - timedelta(days=10)))  # deleted on GitHub: pruned
    seed_doc(pg_dsn, conn_id, "issue:11:4", iso(now - timedelta(days=400)))  # outside the window: kept
    result = sync(factory, conn_id)
    assert paths(published) == [2]
    assert published[0]["routing_key"] == "rag.github_pr_updated"
    assert result["removed"] == 1
    assert set(docs(pg_dsn, conn_id)) == {"issue:11:1", "pr:11:2", "issue:11:4"}
    row = repos(pg_dsn, conn_id)[11]
    assert row["last_full_scan_at"] > now - timedelta(minutes=1)
    since = gh.calls("/repos/octo/a/issues")[0].url.params["since"]
    assert since < iso(now - timedelta(days=364))


def test_incomplete_full_scan_prunes_nothing(gh, published, factory, pg_dsn, monkeypatch):
    monkeypatch.setattr(settings, "max_github_items_per_sync", 1)
    _, conn_id = seed_conn(pg_dsn)
    now = datetime.now(timezone.utc)
    seed_repo(pg_dsn, conn_id, repo(11, "a"), tree_sha="empty-tree")
    gh.installations = {1: [repo(11, "a")]}
    first, second = iso(now - timedelta(days=30)), iso(now - timedelta(days=20))
    gh.issues["octo/a"] = [issue(1, first), issue(2, second)]
    seed_doc(pg_dsn, conn_id, "issue:11:3", iso(now - timedelta(days=10)))
    result = sync(factory, conn_id)
    assert result == {"published": 1, "truncated": True, "removed": 0}
    assert "issue:11:3" in docs(pg_dsn, conn_id)
    row = repos(pg_dsn, conn_id)[11]
    assert row["last_full_scan_at"] is None  # resumes as a full scan next time
    assert iso(row["issues_cursor"]) == first

    # A rate limit part-way through the window is incomplete too.
    monkeypatch.setattr(settings, "max_github_items_per_sync", 500)
    gh.per_page = 1
    gh.fail[("/repos/octo/a/issues", 2)] = 502
    result = sync(factory, conn_id)
    assert result["truncated"] is True and result["removed"] == 0
    assert "issue:11:3" in docs(pg_dsn, conn_id)
    assert repos(pg_dsn, conn_id)[11]["last_full_scan_at"] is None

    # Completing the window finally prunes it.
    gh.fail.clear()
    result = sync(factory, conn_id)
    assert result["removed"] == 1 and result["truncated"] is False
    assert repos(pg_dsn, conn_id)[11]["last_full_scan_at"] is not None


def test_full_scan_confirms_missing_items_before_deleting(gh, published, factory, pg_dsn):
    # Page-numbered listings can skip an item that moved while the scan paged: each document the
    # listing didn't return is checked on its own before it is deleted.
    _, conn_id = seed_conn(pg_dsn)
    now = datetime.now(timezone.utc)
    seed_repo(pg_dsn, conn_id, repo(11, "a"), tree_sha="empty-tree")
    gh.installations = {1: [repo(11, "a")]}
    v = iso(now - timedelta(days=10))
    same_repo = "https://gh.test/repos/octo/a"
    gh.hidden[("octo/a", 3)] = {**issue(3, v), "repository_url": same_repo}  # skipped, unchanged: kept
    gh.hidden[("octo/a", 6)] = {**issue(6, iso(now - timedelta(days=1)), pr=True), "repository_url": same_repo}
    gh.hidden[("octo/a", 8)] = {**issue(8, v), "repository_url": "https://gh.test/repos/octo/elsewhere"}  # moved
    gh.fail[("/repos/octo/a/issues/9", 1)] = 403  # couldn't confirm: kept
    for number in (3, 7, 8, 9):
        seed_doc(pg_dsn, conn_id, f"issue:11:{number}", v)
    seed_doc(pg_dsn, conn_id, "pr:11:6", v)  # skipped by the listing and changed since: re-enqueued
    result = sync(factory, conn_id)
    assert set(docs(pg_dsn, conn_id)) == {"issue:11:3", "issue:11:9", "pr:11:6"}  # 7 (404) and 8 (moved) gone
    assert result["removed"] == 2
    assert [(e["routing_key"], e["payload"]["number"]) for e in published] == [("rag.github_pr_updated", 6)]
    assert repos(pg_dsn, conn_id)[11]["last_full_scan_at"] is not None


def test_cursor_stays_before_an_item_whose_publish_failed(gh, factory, pg_dsn, monkeypatch):
    _, conn_id = seed_conn(pg_dsn)
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    seed_repo(pg_dsn, conn_id, repo(11, "a"), tree_sha="empty-tree", cursor=start,
              full_scan=datetime.now(timezone.utc) - timedelta(days=1))
    gh.installations = {1: [repo(11, "a")]}
    gh.issues["octo/a"] = [issue(1, "2026-10-02T00:00:00Z"), issue(2, "2026-10-03T00:00:00Z"),
                           issue(3, "2026-10-04T00:00:00Z")]
    seed_doc(pg_dsn, conn_id, "issue:11:3", "2026-10-04T00:00:00Z")  # unchanged, after the failing one
    events = []
    real = github_sync.publish_event

    async def flaky(session, routing_key, payload, dedupe_key, org_id=None):
        if payload["number"] == 2:
            # Like publish_event on a broker blip: the event_log row is committed, then it raises.
            await session.commit()
            raise RuntimeError("broker down")
        events.append(payload["number"])
        return await real(session, routing_key, payload, dedupe_key, org_id)

    monkeypatch.setattr(github_sync, "publish_event", flaky)
    with pytest.raises(RuntimeError):
        sync(factory, conn_id)
    assert events == [1]
    row = repos(pg_dsn, conn_id)[11]
    assert row["issues_cursor"] == datetime(2026, 10, 2, tzinfo=timezone.utc)  # at item 1, before item 2
    (conn,) = db_run(pg_dsn, "SELECT github_rr_offset FROM oauth_connections WHERE id = $1", conn_id)
    assert conn["github_rr_offset"] == 1  # rotated even though the sync failed

    seed_doc(pg_dsn, conn_id, "issue:11:1", "2026-10-02T00:00:00Z")  # the worker processed item 1
    recorded = []

    async def record(session, routing_key, payload, dedupe_key, org_id=None):
        recorded.append(payload["number"])
        return await real(session, routing_key, payload, dedupe_key, org_id)

    monkeypatch.setattr(github_sync, "publish_event", record)
    sync(factory, conn_id)  # a normal (not full-scan) sync
    assert recorded == [2]
    assert repos(pg_dsn, conn_id)[11]["issues_cursor"] == datetime(2026, 10, 4, tzinfo=timezone.utc)


def test_rr_offset_saved_when_refresh_fails_mid_sync(gh, published, factory, pg_dsn, monkeypatch):
    _, conn_id = seed_conn(pg_dsn, token="stale")
    gh.installations = {1: [repo(11, "a")]}

    async def refused(connection_id, failed_token=None):
        if failed_token is None:
            return "stale"
        raise HTTPException(status_code=409, detail=RECONNECT)

    monkeypatch.setattr(github_sync, "github_access_token", refused)
    with pytest.raises(HTTPException) as err:
        sync(factory, conn_id)
    assert err.value.status_code == 409
    (conn,) = db_run(pg_dsn, "SELECT github_rr_offset FROM oauth_connections WHERE id = $1", conn_id)
    assert conn["github_rr_offset"] == 1


def test_failed_version_is_skipped_until_changed(gh, published, factory, pg_dsn):
    _, conn_id = seed_conn(pg_dsn)
    now = datetime.now(timezone.utc)
    gh.installations = {1: [repo(11, "a")]}
    gh.trees["octo/a"] = {"sha": "T1", "truncated": False, "tree": [blob("a.md", "s1")]}
    updated = iso(now - timedelta(days=1))
    gh.issues["octo/a"] = [issue(5, updated)]
    for eid, version in (("file:11:a.md", "s1"), ("issue:11:5", updated)):
        db_run(
            pg_dsn,
            "INSERT INTO github_failures (connection_id, external_id, version, attempts) VALUES ($1, $2, $3, 4)",
            conn_id, eid, version,
        )
    assert sync(factory, conn_id)["published"] == 0

    gh.trees["octo/a"] = {"sha": "T2", "truncated": False, "tree": [blob("a.md", "s2")]}
    gh.issues["octo/a"] = [issue(5, iso(now))]
    assert sync(factory, conn_id)["published"] == 2
    assert sorted(map(str, paths(published))) == ["5", "a.md"]


def test_resync_after_remove_documents(gh, published, factory, pg_dsn):
    _, conn_id = seed_conn(pg_dsn)
    gh.installations = {1: [repo(11, "a")]}
    gh.trees["octo/a"] = {"sha": "T1", "truncated": False, "tree": [blob("a.md", "s1")]}
    gh.issues["octo/a"] = [issue(1, iso(datetime.now(timezone.utc) - timedelta(days=1)))]
    assert sync(factory, conn_id)["published"] == 2
    fake_worker(pg_dsn, conn_id, published)
    assert sync(factory, conn_id)["published"] == 0

    async def remove():
        async with factory() as session:
            await connections_routes._delete_documents(session, conn_id)
            await session.commit()

    asyncio.run(remove())
    assert docs(pg_dsn, conn_id) == {}
    before = len(published)
    assert sync(factory, conn_id)["published"] == 2
    assert sorted(map(str, paths(published[before:]))) == ["1", "a.md"]


@pytest.fixture
def worker_dedupe():
    """The worker's own already_processed (services/worker/app/core/idempotency.py), loaded from
    its file against an in-memory Redis, so a test can see what the worker would skip."""
    import importlib.util
    import sys
    import types
    from pathlib import Path

    from fakeredis import FakeAsyncRedis

    fake_config = types.ModuleType("app.core.config")
    fake_config.settings = types.SimpleNamespace(redis_url="memory://", dedupe_ttl_seconds=86400)
    real_config = sys.modules["app.core.config"]
    sys.modules["app.core.config"] = fake_config
    path = Path(__file__).resolve().parents[2] / "worker" / "app" / "core" / "idempotency.py"
    spec = importlib.util.spec_from_file_location("worker_idempotency", path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules["app.core.config"] = real_config  # the api's own, for everything else
    module._redis = FakeAsyncRedis(decode_responses=True)

    def deliver(events) -> list[bool]:
        """already_processed() for each event's dedupe key, as the worker does on delivery."""

        async def go():
            return [await module.already_processed(e["dedupe_key"]) for e in events]

        return asyncio.run(go())

    return deliver


def _sync_and_process(factory, pg_dsn, conn_id, published, worker_dedupe):
    before = len(published)
    sync(factory, conn_id)
    batch = published[before:]
    skipped = worker_dedupe(batch)
    fake_worker(pg_dsn, conn_id, [e for e, dup in zip(batch, skipped) if not dup])
    return batch, skipped


def test_resync_after_reset_is_not_deduped_by_the_worker(gh, published, factory, pg_dsn, worker_dedupe):
    # Within the worker's 24 h dedupe window, content removed by Disconnect-remove (or a repo removed
    # and re-added) must come back on the next sync instead of being skipped as duplicates.
    _, conn_id = seed_conn(pg_dsn)
    gh.installations = {1: [repo(11, "a")]}
    gh.trees["octo/a"] = {"sha": "T1", "truncated": False, "tree": [blob("a.md", "s1")]}
    gh.issues["octo/a"] = [issue(1, iso(datetime.now(timezone.utc) - timedelta(days=1)))]
    batch, skipped = _sync_and_process(factory, pg_dsn, conn_id, published, worker_dedupe)
    assert len(batch) == 2 and skipped == [False, False]

    async def remove():
        async with factory() as session:
            await connections_routes._delete_documents(session, conn_id)
            await session.commit()

    asyncio.run(remove())
    batch, skipped = _sync_and_process(factory, pg_dsn, conn_id, published, worker_dedupe)
    assert len(batch) == 2 and skipped == [False, False]
    assert set(docs(pg_dsn, conn_id)) == {"file:11:a.md", "issue:11:1"}

    # The repository leaves the installation (documents removed), then comes back.
    gh.installations = {1: []}
    sync(factory, conn_id)
    assert docs(pg_dsn, conn_id) == {}
    gh.installations = {1: [repo(11, "a")]}
    batch, skipped = _sync_and_process(factory, pg_dsn, conn_id, published, worker_dedupe)
    assert len(batch) == 2 and skipped == [False, False]
    assert set(docs(pg_dsn, conn_id)) == {"file:11:a.md", "issue:11:1"}


# --------------------------------------------------------------------------- tokens, route, auto_sync


def test_401_refreshes_once_and_retries(gh, published, factory, pg_dsn, monkeypatch):
    _, conn_id = seed_conn(pg_dsn, token="stale")
    gh.installations = {1: [repo(11, "a")]}
    calls = []

    async def fake_token(connection_id, failed_token=None):
        calls.append(failed_token)
        return "stale" if failed_token is None else "tok"

    monkeypatch.setattr(github_sync, "github_access_token", fake_token)
    sync(factory, conn_id)
    assert calls == [None, "stale"]
    assert set(repos(pg_dsn, conn_id)) == {11}


@pytest.fixture
def client(make_client, monkeypatch, factory):
    from fakeredis import FakeAsyncRedis

    monkeypatch.setattr(ratelimit, "get_redis", lambda: FakeAsyncRedis(decode_responses=True))
    c = make_client(db=True, mode="web", routers=[(sync_routes.router, "/sync")])
    c.app.dependency_overrides[require_auth_or_service_token] = lambda: AuthContext(
        org_id=c.org_id, role="owner", user_id=c.user_id
    )
    return c


def test_sync_route_runs_github(gh, published, client, pg_dsn):
    seed_conn(pg_dsn, org_id=client.org_id)
    gh.installations = {1: [repo(11, "a")]}
    gh.trees["octo/a"] = {"sha": "T1", "truncated": False, "tree": [blob("a.md", "s1")]}
    resp = client.post("/sync/github")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"published": 1, "truncated": False, "removed": 0}


def test_disconnected_409(gh, published, client, pg_dsn):
    _, conn_id = seed_conn(pg_dsn, org_id=client.org_id)
    db_run(
        pg_dsn,
        "UPDATE oauth_connections SET access_token = NULL, refresh_token = NULL, disconnected_at = now() WHERE id = $1",
        conn_id,
    )
    resp = client.post("/sync/github")
    assert resp.status_code == 409
    assert resp.json()["detail"] == DISCONNECTED
    assert gh.requests == []


def test_needs_reconnect_409_message(gh, published, client, pg_dsn, monkeypatch):
    # Expiring token, and GitHub refuses the refresh: the sync stops with the reconnect message.
    _, conn_id = seed_conn(pg_dsn, org_id=client.org_id, expires_in=timedelta(minutes=1))
    db_run(pg_dsn, "UPDATE oauth_connections SET needs_reconnect_at = now() WHERE id = $1", conn_id)
    refreshes = []
    real = httpx.AsyncClient

    def refused(request):
        refreshes.append(request)
        return httpx.Response(200, json={"error": "bad_refresh_token"})

    monkeypatch.setattr(
        github_auth.httpx, "AsyncClient", lambda **kw: real(**{"transport": httpx.MockTransport(refused), **kw})
    )
    resp = client.post("/sync/github")
    assert resp.status_code == 409
    assert resp.json()["detail"] == RECONNECT
    assert len(refreshes) == 1  # the flag doesn't pre-block: a refresh was attempted
    assert gh.requests == []


def test_auto_sync_runs_github(gh, published, factory, pg_dsn, monkeypatch):
    _, conn_id = seed_conn(pg_dsn)
    gh.installations = {1: [repo(11, "a")]}
    gh.trees["octo/a"] = {"sha": "T1", "truncated": False, "tree": [blob("a.md", "s1")]}

    @asynccontextmanager
    async def scope():
        async with factory() as session:
            yield session

    async def nothing(session, connection):
        return {"published": 0, "truncated": False, "removed": 0}

    monkeypatch.setattr(auto_sync, "_session_scope", scope)
    monkeypatch.setattr(auto_sync, "sync_notion_connection", nothing)
    monkeypatch.setattr(auto_sync, "sync_jira_connection", nothing)
    asyncio.run(auto_sync._sync_all_connections())
    assert [e["payload"]["path"] for e in published if e["payload"]["connection_id"] == str(conn_id)] == ["a.md"]

"""Worker GitHub handlers (app/tasks/github.py), the REST client (app/integrations/github.py)
and the dead-letter hook (app/tasks/__init__.py's on_dead_letter, called by main.py and
local_queue.py). Real embedded Postgres; GitHub, the sign-in Worker and Voyage are faked."""

import asyncio
import base64
import json
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

from app.core import db, idempotency
from app.core.config import settings
from app.core.crypto import encrypt_token
from app.integrations import broker, github
from app.integrations.github import GithubStatusError
from app.tasks import embed
from conftest import db_run

API = "https://gh.test"
BROKER = "https://broker.test"
RECONNECT = "GitHub needs reconnecting. Use Reconnect on the Connections page."
REPO_ID = 1234
V1 = "2026-10-01T12:00:00Z"
V2 = "2026-10-02T08:30:00Z"


@pytest_asyncio.fixture
async def engine(pg_dsn, monkeypatch):
    monkeypatch.setattr(settings, "token_encryption_key", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "auth_broker_url", BROKER)
    monkeypatch.setattr(settings, "app_mode", "desktop")
    monkeypatch.setattr(settings, "github_api_url", API)
    monkeypatch.setattr(settings, "redis_url", "memory://")
    monkeypatch.setattr(settings, "max_retries", 3)
    monkeypatch.setattr(settings, "database_url", pg_dsn.replace("postgresql://", "postgresql+asyncpg://", 1))
    monkeypatch.setattr(idempotency, "_redis", None)
    await db.init_engine()
    yield
    await db.dispose_engine()
    idempotency._redis = None


class FakeGitHub:
    """Routes by request path (+ query for paginated pages); records every request."""

    def __init__(self):
        self.routes: dict[str, object] = {}
        self.calls: list[httpx.Request] = []
        self.broker_down = False

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if request.url.host == "broker.test":
            if self.broker_down:
                raise httpx.ConnectError("sign-in service unreachable", request=request)
            return httpx.Response(
                200, json={"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 28800}
            )
        page = request.url.params.get("page")
        key = request.url.path + (f"?page={page}" if page and page != "1" else "")
        route = self.routes.get(key)
        if route is None:
            return httpx.Response(404, json={"message": "Not Found"})
        if callable(route):
            return route(request)
        return route

    def github_calls(self):
        return [c for c in self.calls if c.url.host == "gh.test"]


@pytest.fixture
def gh(monkeypatch):
    fake = FakeGitHub()
    transport = httpx.MockTransport(fake.handle)
    monkeypatch.setattr(github, "TRANSPORT", transport)
    monkeypatch.setattr(broker, "TRANSPORT", transport)

    async def no_sleep(seconds):
        return None

    monkeypatch.setattr(github, "_sleep", no_sleep)
    return fake


@pytest.fixture
def embeds(monkeypatch):
    """Fake Voyage: records each batch of texts it was asked to embed."""
    batches: list[list[str]] = []

    async def fake_embed(texts, input_type):
        batches.append(list(texts))
        return [[0.01] * settings.embedding_dimensions for _ in texts]

    monkeypatch.setattr(embed, "embed_texts", fake_embed)
    return batches


async def seed(pg_dsn, *, disconnected=False, expires_in=timedelta(hours=1)):
    org_id, conn_id = uuid.uuid4(), uuid.uuid4()

    def go():
        db_run(pg_dsn, "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T', $2)", org_id, f"t-{org_id}")
        db_run(
            pg_dsn,
            "INSERT INTO oauth_connections (id, org_id, provider, workspace_id, workspace_name, access_token,"
            " refresh_token, expires_at, disconnected_at)"
            " VALUES ($1, $2, 'github', '42', 'octo', $3, $4, $5, $6)",
            conn_id,
            org_id,
            None if disconnected else encrypt_token("A"),
            None if disconnected else encrypt_token("R"),
            None if disconnected else datetime.now(timezone.utc) + expires_in,
            datetime.now(timezone.utc) if disconnected else None,
        )

    await asyncio.to_thread(go)
    return conn_id


async def q(pg_dsn, sql, *args):
    return await asyncio.to_thread(db_run, pg_dsn, sql, *args)


async def doc(pg_dsn, conn_id, external_id):
    rows = await q(
        pg_dsn,
        "SELECT * FROM documents WHERE connection_id = $1 AND source = 'github' AND external_id = $2",
        conn_id,
        external_id,
    )
    return rows[0] if rows else None


async def doc_text(pg_dsn, document_id) -> str:
    rows = await q(pg_dsn, "SELECT content FROM chunks WHERE document_id = $1 ORDER BY chunk_index", document_id)
    return "\n\n".join(r["content"] for r in rows)


def file_payload(conn_id, *, sha="sha1", full_name="octo/repo", path="docs/guide.md", html_url=None):
    return {
        "connection_id": str(conn_id),
        "org_id": str(uuid.uuid4()),
        "repo_id": REPO_ID,
        "full_name": full_name,
        "path": path,
        "sha": sha,
        "html_url": html_url or f"https://github.com/{full_name}/blob/main/{path}",
    }


def item_payload(conn_id, number, *, updated_at=V1, full_name="octo/repo", kind="issues"):
    return {
        "connection_id": str(conn_id),
        "org_id": str(uuid.uuid4()),
        "repo_id": REPO_ID,
        "full_name": full_name,
        "number": number,
        "updated_at": updated_at,
        "html_url": f"https://github.com/{full_name}/{kind}/{number}",
    }


def blob(text: str) -> httpx.Response:
    encoded = base64.b64encode(text.encode()).decode()
    wrapped = "\n".join(encoded[i:i + 60] for i in range(0, len(encoded), 60)) + "\n"  # as GitHub sends it
    return httpx.Response(200, json={"sha": "x", "encoding": "base64", "content": wrapped})


def issue_json(number, title="Crash on save", body="Steps: open, save.", state="open", labels=("bug", "ui"),
               updated_at=V1, **extra):
    return {
        "number": number,
        "title": title,
        "body": body,
        "state": state,
        "labels": [{"name": n} for n in labels],
        "user": {"login": "reporter"},
        "updated_at": updated_at,
        **extra,
    }


def comment(login, body, **extra):
    return {"user": {"login": login}, "body": body, **extra}


def paged(items_page1, items_page2, path):
    """Two pages linked with rel=next."""
    first = httpx.Response(200, json=items_page1, headers={"Link": f'<{API}{path}?per_page=100&page=2>; rel="next"'})
    return {path: first, f"{path}?page=2": httpx.Response(200, json=items_page2)}


# ---------------------------------------------------------------- handlers


@pytest.mark.asyncio
async def test_file_handler_embeds_and_sets_external_version(engine, gh, embeds, pg_dsn):
    from app.tasks.github import handle_github_file_updated

    conn_id = await seed(pg_dsn)
    long_text = "# Guide\n\nInstall with make. " + "Ünïcode prose that wraps past sixty characters. " * 5
    gh.routes["/repos/octo/repo/git/blobs/sha1"] = blob(long_text)
    # A previously dead-lettered version: a success clears it.
    await q(pg_dsn, "INSERT INTO github_failures (connection_id, external_id, version) VALUES ($1, $2, 'sha0')",
            conn_id, f"file:{REPO_ID}:docs/guide.md")
    payload = file_payload(conn_id)
    await handle_github_file_updated(payload)
    assert await q(pg_dsn, "SELECT 1 FROM github_failures WHERE connection_id = $1", conn_id) == []

    d = await doc(pg_dsn, conn_id, f"file:{REPO_ID}:docs/guide.md")
    assert d["external_version"] == "sha1"
    assert d["title"] == "octo/repo · docs/guide.md"
    assert d["url"] == payload["html_url"]
    assert await doc_text(pg_dsn, d["id"]) == long_text.strip()
    (call,) = gh.github_calls()
    assert call.headers["authorization"] == "Bearer A"


@pytest.mark.asyncio
async def test_issue_text_includes_comments(engine, gh, embeds, pg_dsn):
    from app.tasks.github import handle_github_issue_updated

    conn_id = await seed(pg_dsn)
    gh.routes["/repos/octo/repo/issues/7"] = httpx.Response(200, json=issue_json(7))
    gh.routes.update(
        paged([comment("alice", "first comment")], [comment("bob", "second page comment")],
              "/repos/octo/repo/issues/7/comments")
    )
    await handle_github_issue_updated(item_payload(conn_id, 7))

    d = await doc(pg_dsn, conn_id, f"issue:{REPO_ID}:7")
    assert d["title"] == "octo/repo #7: Crash on save"
    assert d["url"] == "https://github.com/octo/repo/issues/7"
    assert d["external_version"] == V1
    assert d["last_edited_at"] == datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    text = await doc_text(pg_dsn, d["id"])
    assert text.startswith("# Crash on save\nState: open · Labels: bug, ui\n\nSteps: open, save.")
    assert "**alice:** first comment" in text
    assert "**bob:** second page comment" in text
    comment_call = next(c for c in gh.github_calls() if c.url.path.endswith("/comments"))
    assert comment_call.url.params["per_page"] == "100"


@pytest.mark.asyncio
async def test_pr_text_includes_conversation_reviews_review_comments_and_files(engine, gh, embeds, pg_dsn):
    from app.tasks.github import handle_github_pr_updated

    conn_id = await seed(pg_dsn)
    base = "/repos/octo/repo"
    gh.routes[f"{base}/pulls/9"] = httpx.Response(
        200, json=issue_json(9, title="Add caching", body="Adds an LRU cache.", labels=("perf",))
    )
    gh.routes[f"{base}/issues/9/comments"] = httpx.Response(200, json=[comment("carol", "Nice idea")])
    gh.routes[f"{base}/pulls/9/reviews"] = httpx.Response(
        200, json=[comment("dave", "Looks good overall", state="APPROVED"), comment("erin", "", state="APPROVED")]
    )
    gh.routes[f"{base}/pulls/9/comments"] = httpx.Response(
        200, json=[comment("frank", "Use a constant here", path="src/cache.py")]
    )
    gh.routes.update(paged([{"filename": "src/cache.py"}], [{"filename": "README.md"}], f"{base}/pulls/9/files"))
    await handle_github_pr_updated(item_payload(conn_id, 9, kind="pull"))

    d = await doc(pg_dsn, conn_id, f"pr:{REPO_ID}:9")
    assert d["title"] == "octo/repo #9: Add caching"
    assert d["external_version"] == V1
    text = await doc_text(pg_dsn, d["id"])
    assert text.startswith("# Add caching\nState: open · Labels: perf\n\nAdds an LRU cache.")
    for expected in ("Conversation", "**carol:** Nice idea", "Reviews", "Looks good overall", "dave",
                     "Review comments", "Use a constant here", "src/cache.py", "Changed files", "README.md"):
        assert expected in text, expected
    assert text.index("Conversation") < text.index("Reviews") < text.index("Review comments") < text.index(
        "Changed files"
    )


@pytest.mark.asyncio
async def test_external_version_not_written_on_failure(engine, gh, embeds, pg_dsn, monkeypatch):
    from app.tasks.github import handle_github_issue_updated

    conn_id = await seed(pg_dsn)
    gh.routes["/repos/octo/repo/issues/7"] = httpx.Response(200, json=issue_json(7))
    gh.routes["/repos/octo/repo/issues/7/comments"] = httpx.Response(200, json=[])
    await handle_github_issue_updated(item_payload(conn_id, 7, updated_at=V1))

    gh.routes["/repos/octo/repo/issues/7"] = httpx.Response(200, json=issue_json(7, body="Changed body", updated_at=V2))

    async def boom(texts, input_type):
        raise RuntimeError("voyage down")

    monkeypatch.setattr(embed, "embed_texts", boom)
    with pytest.raises(RuntimeError):
        await handle_github_issue_updated(item_payload(conn_id, 7, updated_at=V2))
    assert (await doc(pg_dsn, conn_id, f"issue:{REPO_ID}:7"))["external_version"] == V1


@pytest.mark.asyncio
async def test_404_deletes_document_and_acks(engine, gh, embeds, pg_dsn):
    from app.tasks.github import handle_github_file_updated, handle_github_issue_updated

    conn_id = await seed(pg_dsn)
    gh.routes["/repos/octo/repo/git/blobs/sha1"] = blob("hello")
    await handle_github_file_updated(file_payload(conn_id))
    external_id = f"file:{REPO_ID}:docs/guide.md"
    await q(pg_dsn, "INSERT INTO github_failures (connection_id, external_id, version) VALUES ($1, $2, 'old')",
            conn_id, external_id)
    assert await doc(pg_dsn, conn_id, external_id) is not None

    gh.routes.clear()  # blob now 404s
    await handle_github_file_updated(file_payload(conn_id))  # returns normally: acked
    assert await doc(pg_dsn, conn_id, external_id) is None
    assert await q(pg_dsn, "SELECT 1 FROM github_failures WHERE connection_id = $1", conn_id) == []

    # 410 on an issue (deleted) likewise.
    gh.routes["/repos/octo/repo/issues/7"] = httpx.Response(200, json=issue_json(7))
    gh.routes["/repos/octo/repo/issues/7/comments"] = httpx.Response(200, json=[])
    await handle_github_issue_updated(item_payload(conn_id, 7))
    gh.routes["/repos/octo/repo/issues/7"] = httpx.Response(410, json={"message": "This issue was deleted"})
    await handle_github_issue_updated(item_payload(conn_id, 7, updated_at=V2))
    assert await doc(pg_dsn, conn_id, f"issue:{REPO_ID}:7") is None


@pytest.mark.asyncio
async def test_transferred_issue_is_deleted_but_renamed_repo_redirect_is_followed(engine, gh, embeds, pg_dsn):
    from app.tasks.github import handle_github_issue_updated

    conn_id = await seed(pg_dsn)
    # Repository renamed: GitHub redirects to the same repository id → followed.
    gh.routes["/repos/octo/repo/issues/7"] = httpx.Response(
        301, headers={"Location": f"{API}/repositories/{REPO_ID}/issues/7"}
    )
    gh.routes[f"/repositories/{REPO_ID}/issues/7"] = httpx.Response(200, json=issue_json(7))
    gh.routes["/repos/octo/repo/issues/7/comments"] = httpx.Response(200, json=[])
    await handle_github_issue_updated(item_payload(conn_id, 7))
    assert await doc(pg_dsn, conn_id, f"issue:{REPO_ID}:7") is not None

    # Transferred to another repository → gone from this one.
    gh.routes["/repos/octo/repo/issues/7"] = httpx.Response(
        301, headers={"Location": f"{API}/repositories/999/issues/1"}
    )
    await handle_github_issue_updated(item_payload(conn_id, 7, updated_at=V2))
    assert await doc(pg_dsn, conn_id, f"issue:{REPO_ID}:7") is None


@pytest.mark.asyncio
async def test_rename_updates_title_and_url_when_content_unchanged(engine, gh, embeds, pg_dsn):
    from app.tasks.github import handle_github_file_updated, handle_github_issue_updated

    conn_id = await seed(pg_dsn)
    gh.routes["/repos/octo/repo/git/blobs/sha1"] = blob("same content")
    await handle_github_file_updated(file_payload(conn_id))
    gh.routes["/repos/octo/renamed/git/blobs/sha1"] = blob("same content")
    renamed = file_payload(conn_id, full_name="octo/renamed")
    await handle_github_file_updated(renamed)

    d = await doc(pg_dsn, conn_id, f"file:{REPO_ID}:docs/guide.md")
    assert d["title"] == "octo/renamed · docs/guide.md"
    assert d["url"] == renamed["html_url"]
    assert len(embeds) == 1, "unchanged content is not re-embedded"

    # Issue: content unchanged, but a newer updated_at (e.g. a label-less touch) still lands as the version.
    for name in ("repo", "renamed"):
        gh.routes[f"/repos/octo/{name}/issues/7"] = httpx.Response(200, json=issue_json(7))
        gh.routes[f"/repos/octo/{name}/issues/7/comments"] = httpx.Response(200, json=[])
    await handle_github_issue_updated(item_payload(conn_id, 7, updated_at=V1))
    await handle_github_issue_updated(item_payload(conn_id, 7, updated_at=V2, full_name="octo/renamed"))
    d = await doc(pg_dsn, conn_id, f"issue:{REPO_ID}:7")
    assert d["title"] == "octo/renamed #7: Crash on save"
    assert d["url"] == "https://github.com/octo/renamed/issues/7"
    assert d["external_version"] == V2
    assert len(embeds) == 2


@pytest.mark.asyncio
async def test_disconnected_connection_skips(engine, gh, embeds, pg_dsn):
    from app.tasks.github import handle_github_file_updated, handle_github_issue_updated, handle_github_pr_updated

    conn_id = await seed(pg_dsn, disconnected=True)
    await handle_github_file_updated(file_payload(conn_id))
    await handle_github_issue_updated(item_payload(conn_id, 1))
    await handle_github_pr_updated(item_payload(conn_id, 2))
    await handle_github_file_updated(file_payload(uuid.uuid4()))  # connection deleted altogether
    assert gh.calls == []
    assert embeds == []


@pytest.mark.asyncio
async def test_needs_reconnect_raises(engine, gh, embeds, pg_dsn, monkeypatch):
    from app.tasks.github import handle_github_file_updated

    conn_id = await seed(pg_dsn)

    async def refused(connection_id, failed_token=None):
        raise github.GithubNeedsReconnect()

    monkeypatch.setattr(github, "github_access_token", refused)
    with pytest.raises(github.GithubNeedsReconnect, match=RECONNECT):
        await handle_github_file_updated(file_payload(conn_id))


@pytest.mark.asyncio
async def test_401_refreshes_once_then_succeeds(engine, gh, embeds, pg_dsn):
    from app.tasks.github import handle_github_file_updated

    conn_id = await seed(pg_dsn)

    def by_token(request):
        if request.headers["authorization"] == "Bearer new-access":
            return blob("fresh")
        return httpx.Response(401, json={"message": "Bad credentials"})

    gh.routes["/repos/octo/repo/git/blobs/sha1"] = by_token
    await handle_github_file_updated(file_payload(conn_id))
    assert len([c for c in gh.calls if c.url.host == "broker.test"]) == 1
    assert (await doc(pg_dsn, conn_id, f"file:{REPO_ID}:docs/guide.md"))["external_version"] == "sha1"


@pytest.mark.asyncio
async def test_401_twice_fails_after_one_refresh(engine, gh, embeds, pg_dsn):
    from app.tasks.github import handle_github_file_updated

    conn_id = await seed(pg_dsn)
    gh.routes["/repos/octo/repo/git/blobs/sha1"] = httpx.Response(401, json={"message": "Bad credentials"})
    with pytest.raises(github.GithubUnavailable):
        await handle_github_file_updated(file_payload(conn_id))
    assert len([c for c in gh.calls if c.url.host == "broker.test"]) == 1


@pytest.mark.asyncio
async def test_client_retries_5xx_and_rate_limits(engine, gh, embeds, pg_dsn, monkeypatch):
    from app.tasks.github import handle_github_file_updated

    conn_id = await seed(pg_dsn)
    waits = []

    async def record_sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(github, "_sleep", record_sleep)
    answers = iter([
        httpx.Response(502),
        httpx.Response(429, headers={"Retry-After": "7"}),
        httpx.Response(403, headers={"Retry-After": "3"}),  # secondary rate limit
        blob("eventually"),
    ])
    gh.routes["/repos/octo/repo/git/blobs/sha1"] = lambda request: next(answers)
    await handle_github_file_updated(file_payload(conn_id))
    assert waits[1:] == [7.0, 3.0]
    assert (await doc(pg_dsn, conn_id, f"file:{REPO_ID}:docs/guide.md")) is not None


# ---------------------------------------------------------------- dead-letter hook


class FakeMessage:
    def __init__(self, routing_key, payload, retry_count, dedupe_key="dk"):
        self.routing_key = routing_key
        self.body = json.dumps(payload).encode()
        self.headers = {"dedupe_key": dedupe_key, "x-retry-count": retry_count}
        self.content_type = "application/json"
        self.acked = self.rejected = False

    async def ack(self):
        self.acked = True

    async def reject(self, requeue=False):
        self.rejected = True


async def failures(pg_dsn, conn_id):
    return await q(pg_dsn, "SELECT * FROM github_failures WHERE connection_id = $1 ORDER BY external_id", conn_id)


@pytest.fixture
def no_backoff(monkeypatch):
    """main.py's retry branch sleeps the backoff; don't wait it out in tests."""
    from app import main

    async def no_sleep(seconds):
        return None

    monkeypatch.setattr(main.asyncio, "sleep", no_sleep)


async def dead_letter_locally(pg_dsn, routing_key, payload, dedupe_key):
    """One local_queue job on its last attempt, run through _process (real dispatch unless patched)."""
    from app import local_queue

    (event,) = await q(
        pg_dsn,
        "INSERT INTO event_log (routing_key, dedupe_key, payload, status, attempts)"
        " VALUES ($1, $2, $3::jsonb, 'processing', 4) RETURNING id, routing_key, dedupe_key, payload, attempts",
        routing_key, dedupe_key, json.dumps(payload),
    )
    await local_queue._process(tuple(event))
    (row,) = await q(pg_dsn, "SELECT status FROM event_log WHERE id = $1", event["id"])
    return row["status"]


@pytest.mark.asyncio
async def test_dead_letter_records_failure(engine, pg_dsn, monkeypatch, no_backoff):
    from app import local_queue, main

    conn_id = await seed(pg_dsn)

    async def unprocessable(routing_key, payload):
        raise GithubStatusError(422, "/repos/octo/repo/git/blobs/s")

    monkeypatch.setattr(main, "dispatch", unprocessable)
    monkeypatch.setattr(local_queue, "dispatch", unprocessable)

    # main.py (RabbitMQ) path: a message already at max_retries is dead-lettered.
    file_msg = FakeMessage("rag.github_file_updated", file_payload(conn_id, sha="s1"), retry_count=3)
    await main.handle_message(file_msg)
    assert file_msg.rejected
    (f,) = await failures(pg_dsn, conn_id)
    assert (f["external_id"], f["version"], f["attempts"], f["last_error"]) == (
        f"file:{REPO_ID}:docs/guide.md", "s1", 1, "GitHub answered 422 for /repos/octo/repo/git/blobs/s"
    )
    # A message still retrying records nothing new.
    await main.handle_message(FakeMessage("rag.github_file_updated", file_payload(conn_id, sha="s2"), retry_count=0))
    assert (await failures(pg_dsn, conn_id))[0]["version"] == "s1"
    # Dead-lettered again with a new version: attempts += 1, version updated.
    await main.handle_message(FakeMessage("rag.github_file_updated", file_payload(conn_id, sha="s2"), retry_count=3))
    (f,) = await failures(pg_dsn, conn_id)
    assert (f["version"], f["attempts"]) == ("s2", 2)

    # local_queue.py (desktop) path.
    for key, number in (("rag.github_issue_updated", 7), ("rag.github_pr_updated", 9)):
        status = await dead_letter_locally(pg_dsn, key, item_payload(conn_id, number, updated_at=V2), f"k-{number}")
        assert status == "dead_lettered"
    rows = {r["external_id"]: r for r in await failures(pg_dsn, conn_id)}
    assert rows[f"issue:{REPO_ID}:7"]["version"] == V2
    assert rows[f"pr:{REPO_ID}:9"]["version"] == V2


@pytest.mark.asyncio
async def test_unprocessable_item_end_to_end_is_recorded(engine, gh, embeds, pg_dsn):
    """Real handlers: a 422 on the item and undecodable content are the item's own failures."""
    conn_id = await seed(pg_dsn)
    gh.routes["/repos/octo/repo/git/blobs/s422"] = httpx.Response(422, json={"message": "Unprocessable"})
    gh.routes["/repos/octo/repo/git/blobs/sbad"] = httpx.Response(
        200, json={"encoding": "base64", "content": "@@@not base64"}
    )
    for sha, path in (("s422", "a.md"), ("sbad", "b.md")):
        payload = file_payload(conn_id, sha=sha, path=path)
        assert await dead_letter_locally(pg_dsn, "rag.github_file_updated", payload, sha) == "dead_lettered"
    rows = {r["external_id"]: r["version"] for r in await failures(pg_dsn, conn_id)}
    assert rows == {f"file:{REPO_ID}:a.md": "s422", f"file:{REPO_ID}:b.md": "sbad"}


@pytest.mark.asyncio
async def test_broker_unreachable_during_refresh_is_not_recorded(engine, gh, embeds, pg_dsn):
    """Desktop: the sign-in service is down while the token needs renewing → GithubUnavailable
    carrying the broker's message. Not the item's fault, so nothing is recorded."""
    conn_id = await seed(pg_dsn, expires_in=-timedelta(minutes=1))
    gh.broker_down = True
    status = await dead_letter_locally(pg_dsn, "rag.github_file_updated", file_payload(conn_id), "bk")
    assert status == "dead_lettered"
    (event,) = await q(pg_dsn, "SELECT error FROM event_log WHERE dedupe_key = 'bk'")
    assert event["error"] == broker.UNREACHABLE
    assert await failures(pg_dsn, conn_id) == []


@pytest.mark.asyncio
async def test_embedding_failure_is_not_recorded(engine, gh, pg_dsn, monkeypatch):
    conn_id = await seed(pg_dsn)
    gh.routes["/repos/octo/repo/git/blobs/sha1"] = blob("content")

    async def voyage_down(texts, input_type):
        raise httpx.HTTPStatusError(
            "Voyage 503", request=httpx.Request("POST", "https://voyage.test"), response=httpx.Response(503)
        )

    monkeypatch.setattr(embed, "embed_texts", voyage_down)
    status = await dead_letter_locally(pg_dsn, "rag.github_file_updated", file_payload(conn_id), "voy")
    assert status == "dead_lettered"
    assert await failures(pg_dsn, conn_id) == []


@pytest.mark.asyncio
async def test_stale_dead_lettered_job_is_not_recorded(engine, pg_dsn):
    """A job the worker died during has no exception to classify: not recorded."""
    from app import local_queue

    conn_id = await seed(pg_dsn)
    await q(
        pg_dsn,
        "INSERT INTO event_log (routing_key, dedupe_key, payload, status, attempts)"
        " VALUES ('rag.github_file_updated', 'stale-k', $1::jsonb, 'processing', 9)",
        json.dumps(file_payload(conn_id, sha="crashy")),
    )
    async with db.get_session() as session:
        await local_queue.requeue_stale(session)
    (row,) = await q(pg_dsn, "SELECT status FROM event_log WHERE dedupe_key = 'stale-k'")
    assert row["status"] == "dead_lettered"
    assert await failures(pg_dsn, conn_id) == []


@pytest.mark.asyncio
async def test_dead_letter_hook_failure_is_swallowed(engine, pg_dsn, monkeypatch):
    from app import local_queue, main
    from app.tasks import on_dead_letter

    exc = GithubStatusError(422, "/x")

    async def unprocessable(routing_key, payload):
        raise exc

    monkeypatch.setattr(main, "dispatch", unprocessable)
    monkeypatch.setattr(local_queue, "dispatch", unprocessable)
    ghost = uuid.uuid4()  # no such connection: the github_failures insert violates its foreign key

    await on_dead_letter("rag.github_file_updated", file_payload(ghost), str(exc), exc)  # does not raise
    await on_dead_letter("rag.github_file_updated", {"garbage": True}, str(exc), exc)
    await on_dead_letter("rag.notion_page_updated", {"page_id": "p"}, str(exc), exc)  # no hook registered

    msg = FakeMessage("rag.github_file_updated", file_payload(ghost), retry_count=3)
    await main.handle_message(msg)
    assert msg.rejected
    assert await dead_letter_locally(pg_dsn, "rag.github_file_updated", file_payload(ghost), "ghost") == (
        "dead_lettered"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [
        github.GithubNeedsReconnect(),
        github.GithubDisconnected(),
        github.GithubUnavailable(),
        github.GithubUnavailable(broker.UNREACHABLE),
        github.GithubUnavailable(github.API_UNAVAILABLE),
        GithubStatusError(401, "/x"),
        GithubStatusError(403, "/x"),
        GithubStatusError(429, "/x"),
        GithubStatusError(502, "/x"),
        httpx.ConnectError("down"),
        RuntimeError("voyage down"),
        None,
    ],
    ids=repr,
)
async def test_non_item_failures_are_not_recorded(engine, pg_dsn, exc):
    """Not the item's fault: recording them would skip the item until it changes on GitHub, even
    after the cause (connection, GitHub, Voyage, the database) is fixed."""
    from app.tasks import on_dead_letter

    conn_id = await seed(pg_dsn)
    await on_dead_letter("rag.github_file_updated", file_payload(conn_id), str(exc), exc)
    assert await failures(pg_dsn, conn_id) == []


@pytest.mark.asyncio
async def test_same_repo_redirect_loop_raises_and_keeps_document(engine, gh, embeds, pg_dsn):
    from app.tasks.github import handle_github_issue_updated

    conn_id = await seed(pg_dsn)
    gh.routes["/repos/octo/repo/issues/7"] = httpx.Response(200, json=issue_json(7))
    gh.routes["/repos/octo/repo/issues/7/comments"] = httpx.Response(200, json=[])
    await handle_github_issue_updated(item_payload(conn_id, 7))

    loop = httpx.Response(301, headers={"Location": f"{API}/repositories/{REPO_ID}/issues/7"})
    gh.routes["/repos/octo/repo/issues/7"] = loop
    gh.routes[f"/repositories/{REPO_ID}/issues/7"] = loop
    with pytest.raises(GithubStatusError):
        await handle_github_issue_updated(item_payload(conn_id, 7, updated_at=V2))
    assert (await doc(pg_dsn, conn_id, f"issue:{REPO_ID}:7"))["external_version"] == V1

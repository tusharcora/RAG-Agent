"""Migration 015 (GitHub source schema), its schema.py mirror, and the cursor
reset that runs wherever a connection's documents are deleted."""

import asyncio
import uuid

import pytest
from sqlalchemy import select

from app.core import db, schema
from app.integrations import connections as saving
from app.integrations.github_state import reset_github_state
from app.models.rag import OAuthConnection

from .conftest import db_run
from .test_disconnect import ROUTERS, seed, session_factory
from .test_disconnect import client_for  # noqa: F401  (fixture)


def _github_rows(dsn, conn_id):
    repos = db_run(dsn, "SELECT count(*) AS n FROM github_repos WHERE connection_id = $1", conn_id)[0]["n"]
    fails = db_run(dsn, "SELECT count(*) AS n FROM github_failures WHERE connection_id = $1", conn_id)[0]["n"]
    return repos, fails


def seed_github(dsn, org_id, workspace_id="gh-1"):
    conn_id = seed(dsn, org_id, provider="github", workspace_id=workspace_id)
    db_run(
        dsn,
        "INSERT INTO github_repos (connection_id, installation_id, repo_id, full_name, html_url, default_branch,"
        " tree_sha, issues_cursor) VALUES ($1, 1, 10, 'o/r', 'https://github.com/o/r', 'main', 'abc', now())",
        conn_id,
    )
    db_run(
        dsn,
        "INSERT INTO github_failures (connection_id, external_id, version, last_error) VALUES ($1, 'file:10:a.md', 'v1', 'boom')",
        conn_id,
    )
    assert _github_rows(dsn, conn_id) == (1, 1)
    return conn_id


def test_migration_015_applies_on_fresh_db_and_schema_py_is_idempotent(pg_dsn, monkeypatch):
    # pg_dsn already ran desktop/launcher.migrate over every infra/postgres/*.sql, 015 included.
    factory = session_factory(pg_dsn)
    engine = factory.kw["bind"]
    monkeypatch.setattr(db, "_engine", engine)
    asyncio.run(schema.ensure_schema())
    asyncio.run(schema.ensure_schema())

    (r,) = db_run(pg_dsn, "SELECT 'github' = ANY(enum_range(NULL::rag_source)::text[]) AS ok")
    assert r["ok"] is True
    tables = {x["t"] for x in db_run(pg_dsn, "SELECT table_name AS t FROM information_schema.tables")}
    assert {"github_repos", "github_failures"} <= tables
    cols = {
        (x["table_name"], x["column_name"])
        for x in db_run(pg_dsn, "SELECT table_name, column_name FROM information_schema.columns")
    }
    assert ("documents", "external_version") in cols
    assert ("oauth_connections", "needs_reconnect_at") in cols
    assert ("oauth_connections", "github_rr_offset") in cols
    (c,) = db_run(pg_dsn, "SELECT column_default FROM information_schema.columns"
                  " WHERE table_name='oauth_connections' AND column_name='github_rr_offset'")
    assert c["column_default"] == "0"


def test_reset_github_state_on_every_delete_path(client_for, pg_dsn):  # noqa: F811
    client = client_for()

    # Disconnect keeping documents leaves the cursors alone.
    keep = seed_github(pg_dsn, client.org_id)
    assert client.post(f"/connections/{keep}/disconnect", json={"remove_documents": False}).status_code == 200
    assert _github_rows(pg_dsn, keep) == (1, 1)

    # ...and remove-documents afterwards clears them.
    assert client.post(f"/connections/{keep}/remove-documents").status_code == 200
    assert _github_rows(pg_dsn, keep) == (0, 0)


def test_reset_on_disconnect_with_remove(client_for, pg_dsn):  # noqa: F811
    client = client_for()
    conn_id = seed_github(pg_dsn, client.org_id)
    assert client.post(f"/connections/{conn_id}/disconnect", json={"remove_documents": True}).status_code == 200
    assert _github_rows(pg_dsn, conn_id) == (0, 0)


def test_reset_on_reconnect_to_different_workspace_only(client_for, pg_dsn):  # noqa: F811
    client = client_for()
    conn_id = seed_github(pg_dsn, client.org_id, workspace_id="gh-1")

    async def reuse(new_ws):
        async with session_factory(pg_dsn)() as session:
            connection = (await session.execute(select(OAuthConnection).where(OAuthConnection.id == conn_id))).scalar_one()
            await saving._reuse_row(session, connection, new_ws)
            await session.commit()

    asyncio.run(reuse("gh-1"))  # same account: cursors and documents kept
    assert _github_rows(pg_dsn, conn_id) == (1, 1)
    asyncio.run(reuse("gh-2"))
    assert _github_rows(pg_dsn, conn_id) == (0, 0)


def test_reset_github_state_does_not_commit_and_is_scoped(pg_dsn, client_for):  # noqa: F811
    client = client_for()
    a = seed_github(pg_dsn, client.org_id)
    # A second connection (other org: UNIQUE(org_id, provider)).
    other_org = uuid.uuid4()
    db_run(pg_dsn, "INSERT INTO organizations (id, name, slug) VALUES ($1, 'O', $2)", other_org, f"o-{other_org}")
    b = seed_github(pg_dsn, other_org)

    async def go():
        async with session_factory(pg_dsn)() as session:
            await reset_github_state(session, a)
            await session.rollback()  # never committed by the helper

    asyncio.run(go())
    assert _github_rows(pg_dsn, a) == (1, 1)

    async def go2():
        async with session_factory(pg_dsn)() as session:
            await reset_github_state(session, a)
            await session.commit()

    asyncio.run(go2())
    assert _github_rows(pg_dsn, a) == (0, 0)
    assert _github_rows(pg_dsn, b) == (1, 1)

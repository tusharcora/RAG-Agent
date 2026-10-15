"""The MCP list_documents tool's source filter covers every source, GitHub included."""

import asyncio
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.routes import mcp
from app.core.auth import AuthContext

from .conftest import db_run


def test_list_documents_source_enum_includes_github():
    (tool,) = [t for t in mcp.TOOLS if t["name"] == "list_documents"]
    assert tool["inputSchema"]["properties"]["source"]["enum"] == ["notion", "jira", "github"]


def test_list_documents_filters_by_github(pg_dsn):
    org_id = uuid.uuid4()
    db_run(pg_dsn, "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T', $2)", org_id, f"t-{org_id}")
    for provider in ("notion", "github"):
        conn_id = uuid.uuid4()
        db_run(
            pg_dsn,
            "INSERT INTO oauth_connections (id, org_id, provider, workspace_id, workspace_name, access_token)"
            " VALUES ($1, $2, $3, 'w', 'w', 'x')",
            conn_id, org_id, provider,
        )
        db_run(
            pg_dsn,
            "INSERT INTO documents (id, source, external_id, connection_id, title, url)"
            " VALUES ($1, $2::rag_source, $3, $4, $5, 'https://x')",
            uuid.uuid4(), provider, f"{provider}-1", conn_id, f"{provider} doc",
        )

    async def go():
        engine = create_async_engine(pg_dsn.replace("postgresql://", "postgresql+asyncpg://", 1), poolclass=NullPool)
        try:
            async with async_sessionmaker(engine, class_=AsyncSession)() as session:
                return await mcp._list_documents({"source": "github"}, AuthContext(org_id=org_id, role="service"), session)
        finally:
            await engine.dispose()

    result = asyncio.run(go())
    assert [d["title"] for d in result["structuredContent"]["documents"]] == ["github doc"]

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

WORKER_DIR = Path(__file__).resolve().parent.parent
REPO = WORKER_DIR.parent.parent
sys.path.insert(0, str(WORKER_DIR))


@pytest.fixture(scope="session")
def pg_dsn():
    """A real embedded Postgres (pgserver) with the repo's schema applied, shared by the session."""
    import pgserver

    sys.path.insert(0, str(REPO / "desktop"))
    from launcher import migrate

    tmp = tempfile.mkdtemp(prefix="ka-worker-pg-", dir="/tmp")
    server = pgserver.get_server(Path(tmp) / "pgdata", cleanup_mode="stop")
    uri = server.get_uri()
    asyncio.run(migrate(uri, REPO / "infra" / "postgres"))
    try:
        yield uri
    finally:
        server.cleanup()
        shutil.rmtree(tmp, ignore_errors=True)


def db_run(dsn, sql, *args):
    """Run one statement against the test database (own event loop); returns fetched rows."""
    import asyncpg

    async def go():
        conn = await asyncpg.connect(dsn)
        try:
            return await conn.fetch(sql, *args)
        finally:
            await conn.close()

    return asyncio.run(go())

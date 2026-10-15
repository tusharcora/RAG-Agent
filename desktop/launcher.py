"""Run the Knowledge Agent as a single-user desktop app.

Starts an embedded Postgres (pgserver: PG 16 + pgvector, no install needed),
applies the schema, then runs the API (which also serves the built UI) and
the worker as two child processes. No Docker, RabbitMQ or Redis: the worker
polls event_log as its queue, and each process keeps an in-memory Redis.

    python desktop/launcher.py            # opens the app in your browser
    python desktop/launcher.py --no-browser --port 47821

Everything the app stores lives in one folder (printed at startup):
the database, and a secrets.json holding this install's signing and
encryption keys. Delete that folder to start fresh.

The api and worker are run as separate processes because both services'
packages are named `app`; they can't share one interpreter.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
API_DIR = REPO / "services" / "api"
WORKER_DIR = REPO / "services" / "worker"
SQL_DIR = REPO / "infra" / "postgres"
STATIC_DIR = REPO / "services" / "frontend" / "out"

APP_NAME = "Knowledge Agent"
DEFAULT_PORT = 47821


def data_dir() -> Path:
    override = os.environ.get("KNOWLEDGE_AGENT_HOME")
    if override:
        path = Path(override)
    else:
        from platformdirs import user_data_dir

        path = Path(user_data_dir(APP_NAME, appauthor=False))
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_secrets(home: Path) -> dict:
    """Per-install keys, generated once. Losing this file logs everyone out and
    makes stored integration tokens unreadable (they'd need reconnecting)."""
    path = home / "secrets.json"
    if path.exists():
        return json.loads(path.read_text())
    from cryptography.fernet import Fernet

    values = {
        "jwt_secret": secrets.token_urlsafe(48),
        "token_encryption_key": Fernet.generate_key().decode(),
    }
    path.write_text(json.dumps(values, indent=2))
    try:
        path.chmod(0o600)
    except OSError:
        pass  # Windows: ACLs, not mode bits
    return values


def free_port(preferred: int) -> int:
    for candidate in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", candidate))
            except OSError:
                continue
            return s.getsockname()[1]
    raise RuntimeError("no free port")


# --- Database -------------------------------------------------------------

# pgserver's Postgres build ships pgvector but not the uuid-ossp contrib
# module. The schema only uses uuid_generate_v4(), so a one-line shim over
# Postgres's built-in gen_random_uuid() stands in for the whole extension.
UUID_SHIM = """
CREATE OR REPLACE FUNCTION uuid_generate_v4() RETURNS uuid
LANGUAGE sql VOLATILE AS 'SELECT gen_random_uuid()';
"""


def _desktop_sql(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines() if 'EXTENSION IF NOT EXISTS "uuid-ossp"' not in line
    )


async def migrate(dsn: str) -> None:
    """Apply infra/postgres/*.sql in filename order, each exactly once —
    the same files Docker's initdb runs for the server version."""
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(UUID_SHIM)
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " filename TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        done = {r["filename"] for r in await conn.fetch("SELECT filename FROM schema_migrations")}
        for path in sorted(SQL_DIR.glob("*.sql")):
            if path.name in done:
                continue
            print(f"  applying {path.name}")
            async with conn.transaction():
                await conn.execute(_desktop_sql(path.read_text()))
                await conn.execute("INSERT INTO schema_migrations (filename) VALUES ($1)", path.name)
    finally:
        await conn.close()


def _space_free_socket_dirs() -> None:
    """pgserver passes the socket dir to postgres unquoted (`-o -k <dir>`), and
    by default that dir is the data dir itself — which on macOS lives under
    "Application Support", with a space. Point the socket at a short,
    space-free temp dir instead whenever the default one has a space in it."""
    import hashlib
    import tempfile

    from pgserver import postgres_server

    original = postgres_server.find_suitable_socket_dir

    def patched(pgdata, runtime_path):
        chosen = original(pgdata, runtime_path)
        if " " not in str(chosen):
            return chosen
        tag = hashlib.sha256(str(pgdata).encode()).hexdigest()[:10]
        alt = Path(tempfile.gettempdir()) / f"ka-pg-{tag}"
        if " " in str(alt):
            alt = Path("/tmp") / f"ka-pg-{tag}"
        alt.mkdir(parents=True, exist_ok=True)
        return alt

    postgres_server.find_suitable_socket_dir = patched


def start_database(home: Path):
    import pgserver

    if os.name != "nt":
        _space_free_socket_dirs()

    # cleanup_mode="stop": Postgres shuts down when this launcher exits, but
    # the data stays on disk for next time.
    server = pgserver.get_server(home / "pgdata", cleanup_mode="stop")
    uri = server.get_uri()  # postgresql://postgres:@/postgres?host=<socket dir>  (TCP on Windows)
    return server, uri


# --- Processes ------------------------------------------------------------


def child_env(base_url: str, db_uri: str, keys: dict) -> dict:
    env = dict(os.environ)
    env.update(
        {
            "APP_MODE": "desktop",
            "ENVIRONMENT": "desktop",
            "DATABASE_URL": db_uri.replace("postgresql://", "postgresql+asyncpg://", 1),
            "QUEUE_BACKEND": "postgres",
            "REDIS_URL": "memory://",
            "RABBITMQ_URL": "amqp://unused/",
            "EMBEDDING_PROVIDER": env.get("EMBEDDING_PROVIDER", "ollama"),
            "OTEL_ENABLED": "false",
            # UI and API share one plain-http 127.0.0.1 origin.
            "COOKIE_SECURE": "false",
            "COOKIE_SAMESITE": "lax",
            "FRONTEND_URL": base_url,
            "CORS_ORIGINS": json.dumps([base_url]),
            "JWT_SECRET": keys["jwt_secret"],
            "TOKEN_ENCRYPTION_KEY": keys["token_encryption_key"],
            "DESKTOP_STATIC_DIR": str(STATIC_DIR),
            "PYTHONUNBUFFERED": "1",
        }
    )
    return env


def wait_healthy(url: str, proc: subprocess.Popen, timeout: float = 60) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("the API process exited during startup (see the log above)")
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return
        except OSError:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"the API didn't come up at {url}")


def running_instance(home: Path) -> str | None:
    """URL of an already-running launcher for this data dir, if any. Two
    launchers on one data dir would share a Postgres that the first one to
    exit then stops underneath the other."""
    path = home / "running.json"
    try:
        info = json.loads(path.read_text())
        with urllib.request.urlopen(f"{info['url']}/api/health", timeout=2) as r:
            if r.status == 200:
                return info["url"]
    except (OSError, ValueError, KeyError):
        pass
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", DEFAULT_PORT)))
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    home = data_dir()
    print(f"{APP_NAME}: data in {home}")
    existing = running_instance(home)
    if existing:
        print(f"Already running at {existing}")
        if not args.no_browser:
            webbrowser.open(existing)
        return 0
    keys = load_secrets(home)

    print("Starting database…")
    db, db_uri = start_database(home)
    asyncio.run(migrate(db_uri))

    port = free_port(args.port)
    base_url = f"http://127.0.0.1:{port}"
    env = child_env(base_url, db_uri, keys)
    if not STATIC_DIR.is_dir():
        print(
            "  note: no built UI found — run `DESKTOP_BUILD=1 npm run build` in services/frontend.\n"
            "  The API still runs; the UI won't be served until then."
        )

    py = sys.executable
    api = subprocess.Popen(
        [py, "-m", "uvicorn", "app.desktop:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=API_DIR,
        env=env,
    )
    worker = subprocess.Popen([py, "-m", "app.main"], cwd=WORKER_DIR, env=env)
    children = [api, worker]

    def shutdown(*_):
        for p in children:
            if p.poll() is None:
                p.terminate()
        for p in children:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
        db.cleanup()
        (home / "running.json").unlink(missing_ok=True)

    try:
        wait_healthy(f"{base_url}/api/health", api)
        (home / "running.json").write_text(json.dumps({"url": base_url, "pid": os.getpid()}))
        print(f"Ready: {base_url}")
        if not args.no_browser:
            webbrowser.open(base_url)
        signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt))
        while all(p.poll() is None for p in children):
            time.sleep(1)
        name = "API" if api.poll() is not None else "worker"
        print(f"The {name} process stopped; shutting down.")
        return 1
    except KeyboardInterrupt:
        return 0
    except RuntimeError as exc:
        print(f"Error: {exc}")
        return 1
    finally:
        shutdown()


if __name__ == "__main__":
    sys.exit(main())

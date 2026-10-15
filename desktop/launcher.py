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
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import desktop_config  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

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


@dataclass(frozen=True)
class Paths:
    api_dir: Path
    worker_dir: Path
    sql_dir: Path
    ui_dir: Path


def resolve_paths(resources: Path | None) -> Paths:
    """Where the api, worker, schema and built UI live: inside an app bundle's
    resources folder when given one, else this repo's layout."""
    if resources is not None:
        return Paths(resources / "api", resources / "worker", resources / "sql", resources / "ui")
    return Paths(
        REPO / "services" / "api",
        REPO / "services" / "worker",
        REPO / "infra" / "postgres",
        REPO / "services" / "frontend" / "out",
    )


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


async def migrate(dsn: str, sql_dir: Path) -> None:
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
        for path in sorted(sql_dir.glob("*.sql")):
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


def child_env(base_url: str, db_uri: str, home: Path, paths: Paths) -> dict:
    keys = desktop_config.load_secrets(home)
    config = desktop_config.load(home)
    env = dict(os.environ)
    env.update(
        {
            "APP_MODE": "desktop",
            "ENVIRONMENT": "desktop",
            "DATABASE_URL": db_uri.replace("postgresql://", "postgresql+asyncpg://", 1),
            "QUEUE_BACKEND": "postgres",
            "REDIS_URL": "memory://",
            "RABBITMQ_URL": "amqp://unused/",
            "EMBEDDING_PROVIDER": config["embedding_provider"],
            "OTEL_ENABLED": "false",
            # UI and API share one plain-http 127.0.0.1 origin.
            "COOKIE_SECURE": "false",
            "COOKIE_SAMESITE": "lax",
            "FRONTEND_URL": base_url,
            "CORS_ORIGINS": json.dumps([base_url]),
            "JWT_SECRET": keys["jwt_secret"],
            "TOKEN_ENCRYPTION_KEY": keys["token_encryption_key"],
            "DESKTOP_DATA_DIR": str(home),
            "DESKTOP_STATIC_DIR": str(paths.ui_dir),
            "PYTHONUNBUFFERED": "1",
        }
    )
    for secret_key, env_key in (("voyage_api_key", "VOYAGE_API_KEY"), ("google_api_key", "GOOGLE_API_KEY")):
        if keys.get(secret_key):
            env[env_key] = keys[secret_key]
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
    parser.add_argument("--resources", type=Path, help="app bundle resources dir (api/, worker/, sql/, ui/); default: this repo")
    args = parser.parse_args()
    paths = resolve_paths(args.resources)

    home = data_dir()
    print(f"{APP_NAME}: data in {home}")
    existing = running_instance(home)
    if existing:
        print(f"Already running at {existing}")
        if not args.no_browser:
            webbrowser.open(existing)
        return 0
    desktop_config.load_secrets(home)  # generate keys on first run

    print("Starting database…")
    db, db_uri = start_database(home)
    asyncio.run(migrate(db_uri, paths.sql_dir))

    port = free_port(args.port)
    base_url = f"http://127.0.0.1:{port}"
    env = child_env(base_url, db_uri, home, paths)
    if not paths.ui_dir.is_dir():
        print(
            "  note: no built UI found — run `DESKTOP_BUILD=1 npm run build` in services/frontend.\n"
            "  The API still runs; the UI won't be served until then."
        )

    py = sys.executable
    api = subprocess.Popen(
        [py, "-m", "uvicorn", "app.desktop:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=paths.api_dir,
        env=env,
    )
    worker = subprocess.Popen([py, "-m", "app.main"], cwd=paths.worker_dir, env=env)
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

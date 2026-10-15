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
import threading
import webbrowser
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import desktop_config  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

APP_NAME = "Knowledge Agent"
DEFAULT_PORT = 47821


# Windows only: the data folder sits under a publisher folder,
# %LOCALAPPDATA%\tusharcora\Knowledge Agent. Tauri's NSIS installer
# (installMode currentUser) installs the app itself to
# %LOCALAPPDATA%\Knowledge Agent, which is where appauthor=False would put
# the data too: the database and secrets would sit beside the binaries, at the
# mercy of the installer's update/uninstall cleanup.
APP_AUTHOR_WINDOWS = "tusharcora"


def _is_windows() -> bool:
    return sys.platform == "win32"


def data_dir() -> Path:
    override = os.environ.get("KNOWLEDGE_AGENT_HOME")
    if override:
        path = Path(override)
    else:
        from platformdirs import user_data_dir

        # macOS/Linux: ~/Library/Application Support/Knowledge Agent,
        # ~/.local/share/Knowledge Agent (no publisher folder; unchanged).
        author = APP_AUTHOR_WINDOWS if _is_windows() else False
        path = Path(user_data_dir(APP_NAME, appauthor=author))
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
        resources = resources.resolve()  # children run with a different cwd
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


def _drop_dead_handles(pgdata: Path) -> None:
    """pgserver stops Postgres only when the last process in its handle list
    (pgdata/.handle_pids.json) lets go. A launcher that was killed never
    removes itself, so its stale pid would keep Postgres running after every
    later launcher exits. Only one launcher runs per data dir (see
    running_instance), so entries for processes that no longer exist are
    dropped before taking a handle."""
    path = pgdata / ".handle_pids.json"
    try:
        pids = json.loads(path.read_text())
    except (OSError, ValueError):
        return
    if not isinstance(pids, list):
        return
    alive = [p for p in pids if isinstance(p, int) and _pid_alive(p)]
    if alive != pids:
        path.write_text(json.dumps(alive))


def start_database(home: Path):
    import pgserver

    if os.name != "nt":
        _space_free_socket_dirs()

    _drop_dead_handles(home / "pgdata")
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
            # Children exit if this process disappears without stopping them.
            "KA_LAUNCHER_PID": str(os.getpid()),
            "PYTHONUNBUFFERED": "1",
        }
    )
    # Provider keys come only from this install's secrets.json, never from
    # whatever shell or app started the launcher.
    for secret_key, env_key in (("voyage_api_key", "VOYAGE_API_KEY"), ("google_api_key", "GOOGLE_API_KEY")):
        env.pop(env_key, None)
        if keys.get(secret_key):
            env[env_key] = keys[secret_key]
    # The search model's installed name, which setup records when Ollama has
    # it under a tag other than :latest (e.g. "nomic-embed-text:v1.5").
    if config.get("ollama_embed_model"):
        env["OLLAMA_EMBED_MODEL"] = config["ollama_embed_model"]
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


def _health_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/api/health", timeout=2) as r:
            return r.status == 200
    except OSError:
        return False


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return False
        try:
            return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == 0x102  # WAIT_TIMEOUT
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _clear_orphans(url: str, api_answering: bool = True) -> None:
    """Stop a backend whose launcher died without stopping it (a launcher
    from before children watched for that, or a hard kill). POSIX only:
    finds the API by the port it listens on, and the worker by this
    checkout's exact worker command. Postgres is left alone; pgserver reuses
    a running server for the same data dir and stops it with the new launcher."""
    if os.name == "nt":
        return
    port = url.rsplit(":", 1)[-1]
    pids: set[int] = set()
    # Only take the port's listener when it answers as our API: the port may
    # since have been given to something else entirely.
    if api_answering:
        try:
            out = subprocess.run(["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"], capture_output=True, text=True, timeout=5).stdout
            pids |= {int(p) for p in out.split() if p.isdigit()}
        except (OSError, subprocess.SubprocessError):
            pass
    worker_cmd = f"{sys.executable} -m app.main"
    try:
        out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=5).stdout
        for line in out.splitlines():
            pid, _, command = line.strip().partition(" ")
            if command.strip() == worker_cmd and pid.isdigit():
                pids.add(int(pid))
    except (OSError, subprocess.SubprocessError):
        pass
    pids.discard(os.getpid())
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    deadline = time.time() + 5
    while time.time() < deadline and _health_ok(url):
        time.sleep(0.2)
    for pid in pids:
        if _pid_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass


def running_instance(home: Path) -> str | None:
    """URL of an already-running launcher for this data dir, if any. Two
    launchers on one data dir would share a Postgres that the first one to
    exit then stops underneath the other. A backend still answering after
    its launcher died is an orphan: it's stopped, and None is returned."""
    path = home / "running.json"
    try:
        info = json.loads(path.read_text())
        url, pid = info["url"], int(info["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    answering = _health_ok(url)
    if _pid_alive(pid):
        return url if answering else None
    if answering:
        print(f"Stopping a leftover backend at {url} (its launcher had exited)", flush=True)
    _clear_orphans(url, api_answering=answering)
    path.unlink(missing_ok=True)
    return None


# --- Supervisor -----------------------------------------------------------

# The API exits with this code to ask for a restart (after first-run setup
# changes its env). EX_TEMPFAIL from sysexits.h.
RESTART_CODE = 75
LOG_LINES = 40
# Seconds a child gets between terminate and kill. Kept well under the
# desktop shell's 15 s quit grace, which also has to cover stopping Postgres.
CHILD_GRACE = 5


class Supervisor:
    """Runs the child processes, restarts them on request and tears
    everything down exactly once.

    `commands` maps a name to (argv, cwd) and must include "api"; the API is
    the one health-checked and the only one allowed to ask for a restart.
    `env_factory` is called on every (re)start, so a restart picks up config
    the API just saved. `on_stop` runs once, after every child is gone — it is
    where the launcher stops Postgres, which a restart never touches.
    """

    def __init__(
        self,
        commands: dict[str, tuple[list[str], Path]],
        env_factory: Callable[[], dict],
        health_url: str,
        on_stop: Callable[[], None],
        startup_timeout: float = 60,
    ):
        if "api" not in commands:
            raise ValueError("commands must include 'api'")
        self.commands = commands
        self.env_factory = env_factory
        self.health_url = health_url
        self.on_stop = on_stop
        self.startup_timeout = startup_timeout
        self.logs: dict[str, deque[str]] = {name: deque(maxlen=LOG_LINES) for name in commands}
        self.procs: dict[str, subprocess.Popen] = {}
        self._readers: dict[str, threading.Thread] = {}
        self._quit = threading.Event()
        self._lock = threading.RLock()
        self._print_lock = threading.Lock()
        self._stopped = False
        self._handlers_installed = False
        self._old_handlers: dict[int, object] = {}

    # -- public --

    @property
    def stop_requested(self) -> bool:
        return self._quit.is_set()

    def request_stop(self) -> None:
        """Ask run() to shut down and return 0. Safe from any thread or a
        signal handler."""
        self._quit.set()

    def install_handlers(self, lifeline: bool) -> None:
        """SIGINT/SIGTERM (and stdin EOF when `lifeline`) request a clean
        stop. Idempotent; main() calls it before start() so a quit during
        startup is honoured too. Signal handlers need the main thread and are
        skipped elsewhere (tests)."""
        if self._handlers_installed:
            return
        self._handlers_installed = True
        if threading.current_thread() is threading.main_thread():
            # SIGHUP: the Terminal window running us was closed.
            for sig in (signal.SIGINT, signal.SIGTERM, *([signal.SIGHUP] if hasattr(signal, "SIGHUP") else [])):
                self._old_handlers[sig] = signal.signal(sig, lambda *_: self.request_stop())
        if lifeline:
            threading.Thread(target=self._watch_stdin, name="lifeline", daemon=True).start()

    def start(self) -> None:
        """Spawn every child with a fresh env and wait for the API's health
        check. On failure the children are terminated and RuntimeError
        carries the API's last output. Returns early if a stop is requested."""
        with self._lock:
            if self._stopped:
                return
            self._spawn(self.env_factory())
        try:
            self._wait_healthy()
        except RuntimeError:
            self._terminate_children()
            raise

    def run(self, lifeline: bool) -> int:
        """Block until shutdown. 0 for a requested stop (signal, lifeline EOF,
        request_stop), 1 when a child dies or a restart fails. Always ends
        with stop()."""
        self.install_handlers(lifeline)
        try:
            while True:
                if self._quit.wait(0.25):
                    self._say("Stopping…")
                    return 0
                api = self.procs.get("api")
                if api is not None and api.poll() == RESTART_CODE:
                    self._drain("api")
                    self._say("Restarting the API and worker…")
                    self._terminate_children()
                    try:
                        self.start()
                    except RuntimeError as exc:
                        self._say(f"Error: {exc}")
                        return 1
                    if not self._quit.is_set():
                        self._say("Restarted.")
                    continue
                for name, proc in self.procs.items():
                    if proc.poll() is not None:
                        self._drain(name)
                        self._say(f"The {name} process stopped (exit code {proc.returncode}); shutting down.")
                        return 1
        finally:
            self.stop()
            self._restore_handlers()

    def stop(self) -> None:
        """Terminate every child (CHILD_GRACE, then kill), then on_stop().
        Idempotent."""
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
            self._quit.set()
            try:
                self._terminate_children()
            finally:
                self.on_stop()

    # -- internals --

    def _say(self, message: str) -> None:
        with self._print_lock:
            try:
                print(message, flush=True)
            except (OSError, ValueError):  # our stdout is gone (parent closed the pipe)
                pass

    def _spawn(self, env: dict) -> None:
        if os.name == "nt":
            extra = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        else:
            # Own session: a terminal Ctrl-C reaches only the launcher, which
            # then stops the children in order instead of racing them.
            extra = {"start_new_session": True}
        for name, (argv, cwd) in self.commands.items():
            proc = subprocess.Popen(
                argv,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,  # stdin belongs to the lifeline
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                **extra,
            )
            self.procs[name] = proc
            reader = threading.Thread(target=self._pump, args=(name, proc), name=f"output-{name}", daemon=True)
            reader.start()
            self._readers[name] = reader

    def _pump(self, name: str, proc: subprocess.Popen) -> None:
        # Keep draining even if our own stdout breaks, or the child would
        # block on a full pipe.
        for line in proc.stdout:
            line = line.rstrip("\r\n")
            self.logs[name].append(line)
            self._say(f"[{name}] {line}")
        proc.stdout.close()

    def _drain(self, name: str) -> None:
        reader = self._readers.get(name)
        if reader is not None:
            reader.join(timeout=2)

    def _tail(self, name: str) -> str:
        return "\n".join(self.logs[name])

    def _wait_healthy(self) -> None:
        api = self.procs["api"]
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if self._quit.is_set():
                return
            if api.poll() is not None:
                self._drain("api")
                raise RuntimeError(f"the API exited with code {api.returncode} during startup:\n{self._tail('api')}")
            try:
                with urllib.request.urlopen(self.health_url, timeout=2) as r:
                    if r.status == 200:
                        return
            except OSError:
                pass
            self._quit.wait(0.5)
        raise RuntimeError(
            f"the API didn't answer at {self.health_url} within {self.startup_timeout:g}s:\n{self._tail('api')}"
        )

    def _terminate_children(self) -> None:
        with self._lock:
            procs = list(self.procs.values())
            for proc in procs:
                if proc.poll() is None:
                    proc.terminate()
            deadline = time.monotonic() + CHILD_GRACE
            for proc in procs:
                try:
                    proc.wait(timeout=max(0.0, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            for reader in self._readers.values():
                reader.join(timeout=2)
            self.procs = {}
            self._readers = {}

    def _watch_stdin(self) -> None:
        """Lifeline: the desktop shell holds our stdin open and closes it to
        quit. EOF (or no usable stdin at all) means stop."""
        try:
            stream = sys.stdin.buffer
            while stream.read(4096):
                pass
        except Exception:
            pass
        self.request_stop()

    def _restore_handlers(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            return
        for sig, handler in self._old_handlers.items():
            signal.signal(sig, handler)
        self._old_handlers = {}
        self._handlers_installed = False


# --- Entry point ----------------------------------------------------------


def _interrupt(*_):
    raise KeyboardInterrupt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", DEFAULT_PORT)))
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--resources", type=Path, help="app bundle resources dir (api/, worker/, sql/, ui/); default: this repo")
    parser.add_argument("--lifeline", action="store_true", help="quit when stdin closes (used by the desktop shell)")
    args = parser.parse_args()
    paths = resolve_paths(args.resources)
    # Tests only: fake children from tests/fake_child.py, no Postgres.
    fake = os.environ.get("KA_FAKE_CHILDREN") == "1"

    home = data_dir()
    print(f"{APP_NAME}: data in {home}", flush=True)
    existing = running_instance(home)
    if existing:
        print(f"Already running at {existing}", flush=True)
        if not args.no_browser:
            webbrowser.open(existing)
        return 0
    desktop_config.load_secrets(home)  # generate keys on first run

    # Until the supervisor installs its own handlers, SIGTERM unwinds like
    # Ctrl-C so the finally below still stops Postgres.
    signal.signal(signal.SIGTERM, _interrupt)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, _interrupt)
    running = home / "running.json"
    db = None
    sup = None
    try:
        if fake:
            db_uri = "postgresql://fake@/fake"
        else:
            print("Starting database…", flush=True)
            db, db_uri = start_database(home)
            asyncio.run(migrate(db_uri, paths.sql_dir))

        port = free_port(args.port)
        base_url = f"http://127.0.0.1:{port}"
        if not fake and not paths.ui_dir.is_dir():
            print(
                "  note: no built UI found — run `DESKTOP_BUILD=1 npm run build` in services/frontend.\n"
                "  The API still runs; the UI won't be served until then.",
                flush=True,
            )

        py = sys.executable
        if fake:
            fake_child = str(Path(__file__).resolve().parent / "tests" / "fake_child.py")
            commands = {"api": ([py, fake_child, "--port", str(port)], home), "worker": ([py, fake_child], home)}
        else:
            commands = {
                "api": (
                    [py, "-m", "uvicorn", "app.desktop:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
                    paths.api_dir,
                ),
                "worker": ([py, "-m", "app.main"], paths.worker_dir),
            }

        def on_stop() -> None:
            running.unlink(missing_ok=True)
            if db is not None:
                db.cleanup()

        sup = Supervisor(commands, lambda: child_env(base_url, db_uri, home, paths), f"{base_url}/api/health", on_stop)
        sup.install_handlers(args.lifeline)
        sup.start()
        if not sup.stop_requested:
            running.write_text(json.dumps({"url": base_url, "pid": os.getpid()}))
            print(f"Ready: {base_url}", flush=True)
            if not args.no_browser:
                webbrowser.open(base_url)
        return sup.run(args.lifeline)
    except RuntimeError as exc:
        print(f"Error: {exc}", flush=True)
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        if sup is not None:
            sup.stop()
        elif db is not None:
            db.cleanup()


if __name__ == "__main__":
    sys.exit(main())

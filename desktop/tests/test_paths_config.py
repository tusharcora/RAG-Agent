import os
import stat
import sys
from pathlib import Path

import pytest

DESKTOP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DESKTOP_DIR))

import desktop_config  # noqa: E402
import launcher  # noqa: E402


def test_load_defaults_when_missing(tmp_path):
    assert desktop_config.load(tmp_path) == {"setup_complete": False, "embedding_provider": "ollama"}


def test_save_roundtrip_and_merge(tmp_path):
    desktop_config.save(tmp_path, setup_complete=True)
    cfg = desktop_config.load(tmp_path)
    assert cfg["setup_complete"] is True
    assert cfg["embedding_provider"] == "ollama"


def test_secrets_generated_once_and_private(tmp_path):
    a = desktop_config.load_secrets(tmp_path)
    b = desktop_config.load_secrets(tmp_path)
    assert a == b
    assert a["jwt_secret"] and a["token_encryption_key"]
    if os.name != "nt":
        assert stat.S_IMODE((tmp_path / "secrets.json").stat().st_mode) == 0o600


def test_save_secrets_keeps_existing_keys(tmp_path):
    before = desktop_config.load_secrets(tmp_path)
    desktop_config.save_secrets(tmp_path, voyage_api_key="pa-x")
    after = desktop_config.load_secrets(tmp_path)
    assert after["jwt_secret"] == before["jwt_secret"]
    assert after["voyage_api_key"] == "pa-x"
    if os.name != "nt":
        assert stat.S_IMODE((tmp_path / "secrets.json").stat().st_mode) == 0o600


def test_resolve_paths_resources(tmp_path):
    p = launcher.resolve_paths(tmp_path)
    assert p.api_dir == tmp_path / "api"
    assert p.worker_dir == tmp_path / "worker"
    assert p.sql_dir == tmp_path / "sql"
    assert p.ui_dir == tmp_path / "ui"


def test_resolve_paths_repo_default():
    p = launcher.resolve_paths(None)
    assert p.api_dir.as_posix().endswith("services/api")
    assert p.worker_dir.as_posix().endswith("services/worker")
    assert p.sql_dir.as_posix().endswith("infra/postgres")
    assert p.ui_dir.as_posix().endswith("services/frontend/out")


def test_child_env_maps_config(tmp_path):
    paths = launcher.resolve_paths(None)
    desktop_config.save(tmp_path, embedding_provider="voyage")
    desktop_config.save_secrets(tmp_path, voyage_api_key="pa-x", google_api_key="g-y")
    env = launcher.child_env("http://127.0.0.1:1", "postgresql://u@/db", tmp_path, paths)
    assert env["EMBEDDING_PROVIDER"] == "voyage"
    assert env["VOYAGE_API_KEY"] == "pa-x"
    assert env["GOOGLE_API_KEY"] == "g-y"
    assert env["DESKTOP_DATA_DIR"] == str(tmp_path)
    assert env["DESKTOP_STATIC_DIR"] == str(paths.ui_dir)


def test_child_env_defaults_have_no_keys(tmp_path, monkeypatch):
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    env = launcher.child_env("http://127.0.0.1:1", "postgresql://u@/db", tmp_path, launcher.resolve_paths(None))
    assert env["EMBEDDING_PROVIDER"] == "ollama"
    assert "VOYAGE_API_KEY" not in env
    assert "GOOGLE_API_KEY" not in env


def test_child_env_drops_inherited_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("VOYAGE_API_KEY", "inherited-v")
    monkeypatch.setenv("GOOGLE_API_KEY", "inherited-g")
    paths = launcher.resolve_paths(None)
    env = launcher.child_env("http://127.0.0.1:1", "postgresql://u@/db", tmp_path, paths)
    assert "VOYAGE_API_KEY" not in env
    assert "GOOGLE_API_KEY" not in env
    desktop_config.save_secrets(tmp_path, voyage_api_key="pa-x")
    env = launcher.child_env("http://127.0.0.1:1", "postgresql://u@/db", tmp_path, paths)
    assert env["VOYAGE_API_KEY"] == "pa-x"
    assert "GOOGLE_API_KEY" not in env


def test_child_env_ignores_inherited_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "voyage")
    env = launcher.child_env("http://127.0.0.1:1", "postgresql://u@/db", tmp_path, launcher.resolve_paths(None))
    assert env["EMBEDDING_PROVIDER"] == "ollama"


@pytest.mark.parametrize("windows, author", [(True, "tusharcora"), (False, False)])
def test_data_dir_publisher_folder_only_on_windows(tmp_path, monkeypatch, windows, author):
    import platformdirs

    calls = []

    def fake_user_data_dir(appname, appauthor=None, *a, **kw):
        calls.append((appname, appauthor))
        return str(tmp_path / "data")

    monkeypatch.delenv("KNOWLEDGE_AGENT_HOME", raising=False)
    monkeypatch.setattr(launcher, "_is_windows", lambda: windows)
    monkeypatch.setattr(platformdirs, "user_data_dir", fake_user_data_dir)
    assert launcher.data_dir() == tmp_path / "data"
    assert calls == [("Knowledge Agent", author)]


def test_windows_data_dir_is_not_the_install_dir(monkeypatch):
    """With the publisher folder, the data dir can't be Tauri's NSIS
    per-user install dir (%LOCALAPPDATA%\\Knowledge Agent)."""
    from platformdirs import windows

    local = "C:/Users/t/AppData/Local"
    monkeypatch.setattr(windows, "get_win_folder", lambda csidl: local)
    path = windows.Windows("Knowledge Agent", launcher.APP_AUTHOR_WINDOWS).user_data_dir
    assert Path(path) == Path(local) / "tusharcora" / "Knowledge Agent"
    assert Path(path) != Path(local) / "Knowledge Agent"


def test_data_dir_override(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_AGENT_HOME", str(tmp_path / "home"))
    assert launcher.data_dir() == tmp_path / "home"
    assert (tmp_path / "home").is_dir()


@pytest.fixture
def pg_uri(tmp_path):
    import pgserver

    server = pgserver.get_server(tmp_path / "pgdata", cleanup_mode="stop")
    try:
        yield server.get_uri()
    finally:
        server.cleanup()


def test_migrate_twice_is_idempotent_and_uuid_shim_works(pg_uri):
    import asyncio

    import asyncpg

    sql_dir = DESKTOP_DIR.parent / "infra" / "postgres"
    files = sorted(p.name for p in sql_dir.glob("*.sql"))

    async def scenario():
        await launcher.migrate(pg_uri, sql_dir)
        await launcher.migrate(pg_uri, sql_dir)  # second run: nothing to apply, no error
        conn = await asyncpg.connect(pg_uri)
        try:
            applied = [r["filename"] for r in await conn.fetch("SELECT filename FROM schema_migrations ORDER BY filename")]
            # organizations.id defaults to uuid_generate_v4(), provided by the shim.
            new_id = await conn.fetchval(
                "INSERT INTO organizations (name, slug) VALUES ('Shim', 'shim-test') RETURNING id"
            )
            has_ossp = await conn.fetchval("SELECT count(*) FROM pg_extension WHERE extname = 'uuid-ossp'")
            return applied, new_id, has_ossp
        finally:
            await conn.close()

    applied, new_id, has_ossp = asyncio.run(scenario())
    assert applied == files
    assert new_id is not None and new_id.version == 4
    assert has_ossp == 0, "the shim stands in for the extension"


def test_migrate_skips_already_applied_files(pg_uri, tmp_path):
    """Applied files are tracked by name: an edited file isn't re-run (which is
    why infra/postgres/*.sql must be append-only)."""
    import asyncio

    import asyncpg

    sql_dir = tmp_path / "sql"
    sql_dir.mkdir()
    (sql_dir / "001_a.sql").write_text("CREATE TABLE t_a (id uuid PRIMARY KEY DEFAULT uuid_generate_v4());")

    async def scenario():
        await launcher.migrate(pg_uri, sql_dir)
        (sql_dir / "001_a.sql").write_text("CREATE TABLE t_a (id int);")  # would fail if re-run
        (sql_dir / "002_b.sql").write_text("CREATE TABLE t_b (id int);")
        await launcher.migrate(pg_uri, sql_dir)
        conn = await asyncpg.connect(pg_uri)
        try:
            return await conn.fetchval("SELECT count(*) FROM schema_migrations")
        finally:
            await conn.close()

    assert asyncio.run(scenario()) == 2


def test_child_env_maps_saved_embed_model(tmp_path, monkeypatch):
    monkeypatch.delenv("OLLAMA_EMBED_MODEL", raising=False)
    paths = launcher.resolve_paths(None)
    env = launcher.child_env("http://127.0.0.1:1", "postgresql://u@/db", tmp_path, paths)
    assert "OLLAMA_EMBED_MODEL" not in env
    desktop_config.save(tmp_path, ollama_embed_model="nomic-embed-text:v1.5")
    env = launcher.child_env("http://127.0.0.1:1", "postgresql://u@/db", tmp_path, paths)
    assert env["OLLAMA_EMBED_MODEL"] == "nomic-embed-text:v1.5"

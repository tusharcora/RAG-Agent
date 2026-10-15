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

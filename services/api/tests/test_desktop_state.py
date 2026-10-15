import json
import os
import stat

from app.core import desktop_state
from app.core.config import settings


def test_config_defaults_and_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "desktop_data_dir", str(tmp_path))
    assert desktop_state.load_config() == {"setup_complete": False, "embedding_provider": "ollama"}
    desktop_state.save_config(setup_complete=True)
    assert json.loads((tmp_path / "desktop.json").read_text())["setup_complete"] is True


def test_save_secrets_merges_and_is_private(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "desktop_data_dir", str(tmp_path))
    (tmp_path / "secrets.json").write_text(json.dumps({"jwt_secret": "x"}))
    desktop_state.save_secrets(voyage_api_key="k")
    assert json.loads((tmp_path / "secrets.json").read_text()) == {"jwt_secret": "x", "voyage_api_key": "k"}
    if os.name != "nt":
        assert stat.S_IMODE((tmp_path / "secrets.json").stat().st_mode) == 0o600

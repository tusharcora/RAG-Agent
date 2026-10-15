"""Reads/writes the desktop launcher's desktop.json and secrets.json.

Same file formats as desktop/desktop_config.py (the API can't import that
module: different package root), rooted at settings.desktop_data_dir.
"""

import asyncio
import json
import os
import tempfile
from pathlib import Path

from app.core.config import settings

DEFAULTS = {"setup_complete": False, "embedding_provider": "ollama"}
RESTART_EXIT_CODE = 75


def _home() -> Path:
    return Path(settings.desktop_data_dir)


def _atomic_write(path: Path, text: str, private: bool = False) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        if private:
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass  # Windows: ACLs, not mode bits
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load_config() -> dict:
    return {**DEFAULTS, **_read(_home() / "desktop.json")}


def save_config(**changes) -> None:
    _atomic_write(_home() / "desktop.json", json.dumps({**load_config(), **changes}, indent=2))


def save_secrets(**changes) -> None:
    """Merge into secrets.json (owner-only). The launcher creates the file with
    the generated secrets; never drop what is already there."""
    path = _home() / "secrets.json"
    try:
        existing = json.loads(path.read_text())
    except FileNotFoundError:
        existing = {}
    # Any other failure (unreadable, corrupt) raises: overwriting would drop
    # the generated jwt_secret / token_encryption_key.
    if not isinstance(existing, dict):
        raise ValueError("secrets.json is not a JSON object")
    values = {**existing, **changes}
    _atomic_write(_home() / "secrets.json", json.dumps(values, indent=2), private=True)


def schedule_restart(delay: float = 0.5) -> None:
    """Exit with code 75 shortly, after the response has gone out; the launcher
    treats 75 as 'restart me with the new settings'."""
    asyncio.get_running_loop().call_later(delay, os._exit, RESTART_EXIT_CODE)

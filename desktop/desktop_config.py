"""The user's first-run choices (desktop.json) and per-install keys (secrets.json).

Both live in the app's data folder and are written atomically (temp file +
os.replace) so a crash mid-write never leaves a half-written file.
"""

from __future__ import annotations

import json
import os
import secrets
import tempfile
from pathlib import Path

DEFAULTS = {"setup_complete": False, "embedding_provider": "ollama"}


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


def load(home: Path) -> dict:
    """desktop.json merged over the defaults; a missing or unreadable file means defaults."""
    try:
        stored = json.loads((home / "desktop.json").read_text())
    except (OSError, ValueError):
        stored = {}
    if not isinstance(stored, dict):
        stored = {}
    return {**DEFAULTS, **stored}


def save(home: Path, **changes) -> dict:
    new = {**load(home), **changes}
    _atomic_write(home / "desktop.json", json.dumps(new, indent=2))
    return new


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
    _atomic_write(path, json.dumps(values, indent=2), private=True)
    return values


def save_secrets(home: Path, **changes) -> None:
    values = {**load_secrets(home), **changes}
    _atomic_write(home / "secrets.json", json.dumps(values, indent=2), private=True)

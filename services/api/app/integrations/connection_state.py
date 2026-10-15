"""Shared check for a disconnected Notion/Jira connection (see
POST /connections/{id}/disconnect). Kept dependency-free so sync.py,
jira_auth.py and routes/connections.py can all import it without cycles."""

from fastapi import HTTPException

LABELS = {"notion": "Notion", "jira": "Jira"}


def is_disconnected(connection) -> bool:
    # Tokens are wiped on disconnect; a NULL token alone also means there is nothing to sync with.
    return getattr(connection, "disconnected_at", None) is not None or not connection.access_token


def disconnected_detail(provider: str) -> str:
    return f"{LABELS.get(provider, provider)} is disconnected. Connect it again to sync."


def require_connected(connection, provider: str | None = None) -> None:
    """409 for any token-using path on a disconnected connection."""
    if is_disconnected(connection):
        raise HTTPException(status_code=409, detail=disconnected_detail(provider or connection.provider))

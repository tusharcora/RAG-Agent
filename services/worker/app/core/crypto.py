from cryptography.fernet import Fernet

from app.core.config import settings


def _fernet() -> Fernet:
    # Built per-call, not cached at import time — settings.token_encryption_key is read
    # once at process startup either way (pydantic-settings), but constructing lazily here
    # keeps this module import-safe even before settings is fully configured (tests, etc.)
    # and matches Fernet's own guidance against holding a key object longer than needed.
    return Fernet(settings.token_encryption_key.encode())


def encrypt_token(plaintext: str | None) -> str | None:
    """oauth_connections.access_token/refresh_token are stored encrypted at rest — a
    Postgres dump/leak shouldn't hand over live Notion/Jira credentials in plaintext.
    refresh_token is nullable (Notion tokens don't expire, so notion_oauth.py sets it to
    None); access_token is NOT NULL but briefly holds "" between OAuthConnection(...)
    construction and the real value being assigned later in the same request. Neither
    None nor "" is a secret worth encrypting, so both pass through unchanged — callers
    must not skip this function for the "real" value, only these placeholder ones hit it."""
    if not plaintext:
        return plaintext
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str | None) -> str | None:
    """Inverse of encrypt_token — see its docstring for the None/"" passthrough. Raises
    cryptography.fernet.InvalidToken for any row written before this encryption landed
    (plaintext in, garbage out) or otherwise corrupted ciphertext. Every call site must
    catch that and fail cleanly (HTTPException(400, ...) in the api, a clear log + let the
    task fail/dead-letter in the worker) rather than let it surface as an unhandled 500 —
    existing connections need to be reconnected (re-run OAuth) to get a token this can
    actually decrypt."""
    if not ciphertext:
        return ciphertext
    return _fernet().decrypt(ciphertext.encode()).decode()

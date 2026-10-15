"""Idempotent schema additions applied at API startup.

infra/postgres/*.sql only runs when the Postgres volume is first created, so
tables added later would never reach an existing local database. Each
statement here is IF NOT EXISTS and mirrors a numbered file in infra/postgres.
"""

from sqlalchemy import text

from app.core import db

STATEMENTS = [
    # 010_llm_settings.sql
    """
    CREATE TABLE IF NOT EXISTS org_llm_settings (
        org_id UUID PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,
        provider TEXT NOT NULL DEFAULT 'gemini' CHECK (provider IN ('gemini', 'anthropic', 'openai', 'local')),
        model TEXT,
        base_url TEXT,
        api_key_encrypted TEXT,
        api_key_hint TEXT,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # 011_llm_credentials.sql
    """
    CREATE TABLE IF NOT EXISTS org_llm_credentials (
        org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
        provider TEXT NOT NULL CHECK (provider IN ('anthropic', 'openai', 'local')),
        model TEXT,
        base_url TEXT,
        api_key_encrypted TEXT,
        api_key_hint TEXT,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (org_id, provider)
    )
    """,
    """
    INSERT INTO org_llm_credentials (org_id, provider, model, base_url, api_key_encrypted, api_key_hint)
    SELECT org_id, provider, model, base_url, api_key_encrypted, api_key_hint
    FROM org_llm_settings
    WHERE provider <> 'gemini'
    ON CONFLICT (org_id, provider) DO NOTHING
    """,
]


async def ensure_schema() -> None:
    async with db._engine.begin() as conn:
        for stmt in STATEMENTS:
            await conn.execute(text(stmt))

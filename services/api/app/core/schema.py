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
    # 012_message_sources.sql
    "ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS sources JSONB",
    "ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS cited_indices JSONB",
    "ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS model TEXT",
    "ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS elapsed_ms INTEGER",
    # 013_local_queue.sql
    "ALTER TABLE event_log ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE event_log ADD COLUMN IF NOT EXISTS available_at TIMESTAMPTZ NOT NULL DEFAULT now()",
    "CREATE INDEX IF NOT EXISTS idx_event_log_queue ON event_log (status, available_at)",
    # 014_connection_disconnect.sql
    "ALTER TABLE oauth_connections ADD COLUMN IF NOT EXISTS disconnected_at TIMESTAMPTZ",
    "ALTER TABLE oauth_connections ALTER COLUMN access_token DROP NOT NULL",
    # 015_github_source.sql (nothing here may use the 'github' enum value: it is added in this transaction)
    "ALTER TYPE rag_source ADD VALUE IF NOT EXISTS 'github'",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS external_version TEXT",
    "ALTER TABLE oauth_connections ADD COLUMN IF NOT EXISTS needs_reconnect_at TIMESTAMPTZ",
    "ALTER TABLE oauth_connections ADD COLUMN IF NOT EXISTS github_rr_offset INT NOT NULL DEFAULT 0",
    """
    CREATE TABLE IF NOT EXISTS github_repos (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        connection_id UUID NOT NULL REFERENCES oauth_connections(id) ON DELETE CASCADE,
        installation_id BIGINT NOT NULL,
        repo_id BIGINT NOT NULL,
        full_name TEXT NOT NULL,
        html_url TEXT NOT NULL,
        default_branch TEXT NOT NULL,
        private BOOLEAN NOT NULL DEFAULT false,
        tree_sha TEXT,
        issues_cursor TIMESTAMPTZ,
        last_full_scan_at TIMESTAMPTZ,
        UNIQUE (connection_id, repo_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS github_failures (
        connection_id UUID NOT NULL REFERENCES oauth_connections(id) ON DELETE CASCADE,
        external_id TEXT NOT NULL,
        version TEXT NOT NULL,
        attempts INT NOT NULL DEFAULT 1,
        last_error TEXT,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (connection_id, external_id)
    )
    """,
]


async def ensure_schema() -> None:
    async with db._engine.begin() as conn:
        for stmt in STATEMENTS:
            await conn.execute(text(stmt))

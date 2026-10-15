-- Per-org choice of the model that writes chat answers (see
-- services/api/app/core/llm.py). No row = server default (Gemini).
-- The API also applies this at startup (app/core/schema.py), so existing
-- databases pick it up without a manual migration.

CREATE TABLE IF NOT EXISTS org_llm_settings (
    org_id UUID PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,
    provider TEXT NOT NULL DEFAULT 'gemini' CHECK (provider IN ('gemini', 'anthropic', 'openai', 'local')),
    model TEXT,
    base_url TEXT,
    api_key_encrypted TEXT,
    api_key_hint TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

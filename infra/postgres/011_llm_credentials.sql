-- Saved API keys / model / URL per provider, so switching the active answer
-- model (org_llm_settings.provider) keeps every other provider's key.
-- Also applied at API startup by app/core/schema.py.

CREATE TABLE IF NOT EXISTS org_llm_credentials (
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    provider TEXT NOT NULL CHECK (provider IN ('anthropic', 'openai', 'local')),
    model TEXT,
    base_url TEXT,
    api_key_encrypted TEXT,
    api_key_hint TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, provider)
);

-- Carry over anything saved before this table existed.
INSERT INTO org_llm_credentials (org_id, provider, model, base_url, api_key_encrypted, api_key_hint)
SELECT org_id, provider, model, base_url, api_key_encrypted, api_key_hint
FROM org_llm_settings
WHERE provider <> 'gemini'
ON CONFLICT (org_id, provider) DO NOTHING;

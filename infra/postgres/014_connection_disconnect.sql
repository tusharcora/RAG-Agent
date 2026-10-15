-- Disconnecting a Notion/Jira connection keeps its row (UNIQUE(org_id, provider)
-- lets a later reconnect reuse it) but wipes its tokens and stamps when it was
-- disconnected. Its documents are either deleted at disconnect time or kept,
-- still searchable, until removed or until a reconnect to a different
-- workspace. Also applied at API startup by app/core/schema.py.

ALTER TABLE oauth_connections ADD COLUMN IF NOT EXISTS disconnected_at TIMESTAMPTZ;
ALTER TABLE oauth_connections ALTER COLUMN access_token DROP NOT NULL;

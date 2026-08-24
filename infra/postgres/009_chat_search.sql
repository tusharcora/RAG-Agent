-- Full-text search over past conversations (GET /sessions?search=...), same
-- pattern as 005_document_search.sql. Indexed on chat_messages.content, not
-- chat_sessions.preview -- the preview is just the first ~140 chars of the
-- first user message, so indexing it would miss everything said after the
-- opening line (and every assistant message entirely).

ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS search_vector tsvector
    GENERATED ALWAYS AS (to_tsvector('english', content)) STORED;

CREATE INDEX IF NOT EXISTS idx_chat_messages_search_vector ON chat_messages USING gin (search_vector);

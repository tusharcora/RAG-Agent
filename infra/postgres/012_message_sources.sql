-- Keep each assistant answer's retrieved sources, which of them it cited, and
-- which model wrote it, so reopening a past conversation shows the same
-- sources panel as when it was streamed. NULL on user messages and on answers
-- saved before this column existed. Also applied at API startup by
-- app/core/schema.py.

ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS sources JSONB;
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS cited_indices JSONB;
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS model TEXT;
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS elapsed_ms INTEGER;

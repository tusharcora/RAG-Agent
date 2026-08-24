-- Durable chat history, replacing the Redis-only session_store.py, which was
-- deliberately "short-term memory" (TTL-bound, and lost entirely on a Redis
-- restart since no persistence volume is configured for that container) —
-- fine for retrieval context, not for "log out, log back in, conversation is
-- still there," which is a real product requirement, not short-term memory.

CREATE TABLE chat_sessions (
    id UUID PRIMARY KEY,
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    -- First user message's opening text, set once and never overwritten —
    -- mirrors the old Redis preview key's behavior exactly.
    preview TEXT,
    turn_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- GET /sessions lists newest-active-first, scoped to one user within one org.
CREATE INDEX idx_chat_sessions_user_active ON chat_sessions (org_id, user_id, updated_at DESC);

CREATE TABLE chat_messages (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id UUID NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_chat_messages_session_created ON chat_messages (session_id, created_at);

-- Extensions
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Shared event log: every event the worker processes gets a row here,
-- regardless of which project it belongs to. Useful for the dashboard's
-- "activity feed" and for debugging/replaying failed events.
CREATE TABLE IF NOT EXISTS event_log (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    routing_key TEXT NOT NULL,
    dedupe_key TEXT NOT NULL,
    payload JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'received', -- received | processing | succeeded | failed | dead_lettered
    error TEXT,
    trace_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_event_log_dedupe_key ON event_log (dedupe_key);
CREATE INDEX IF NOT EXISTS idx_event_log_routing_key ON event_log (routing_key);
CREATE INDEX IF NOT EXISTS idx_event_log_status ON event_log (status);

-- Project-specific schemas/tables (document chunks + embeddings for the RAG
-- agent, findings for the code review bot) get added as migrations once
-- each project's data model is designed — this file only sets up what's
-- shared by both.

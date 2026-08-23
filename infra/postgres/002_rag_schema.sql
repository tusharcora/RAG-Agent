-- RAG Knowledge Agent schema: OAuth connections + synced Notion/Jira content
-- as chunked, embedded documents. Runs after init.sql (filename ordering).

CREATE TYPE rag_source AS ENUM ('notion', 'jira');

-- One row per connected workspace/site. Single-tenant: UNIQUE(provider) means
-- reconnecting a provider overwrites the previous connection rather than
-- adding a second one.
CREATE TABLE IF NOT EXISTS oauth_connections (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    provider TEXT NOT NULL UNIQUE, -- 'notion' | 'jira'
    workspace_id TEXT NOT NULL,
    workspace_name TEXT NOT NULL,
    site_url TEXT, -- Jira only
    access_token TEXT NOT NULL,
    refresh_token TEXT, -- null for Notion (tokens don't expire)
    expires_at TIMESTAMPTZ,
    bot_id TEXT, -- Notion only
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per synced Notion page / Jira issue.
CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source rag_source NOT NULL,
    external_id TEXT NOT NULL, -- Notion page_id / Jira issue_id
    connection_id UUID NOT NULL REFERENCES oauth_connections(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    content_hash TEXT, -- sha256 of fetched content; skip re-embed if unchanged
    last_edited_at TIMESTAMPTZ,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source, external_id)
);

-- Chunked + embedded content. voyage-3-lite = 512 dimensions; changing the
-- embedding model later requires a migration + full re-embed of this table.
CREATE TABLE IF NOT EXISTS chunks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INT NOT NULL,
    content TEXT NOT NULL,
    embedding vector(512),
    token_count INT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_chunks_embedding ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_documents_source_external ON documents (source, external_id);
CREATE INDEX IF NOT EXISTS idx_documents_connection ON documents (connection_id);

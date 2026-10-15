-- GitHub as a knowledge source (files, issues, pull requests). Also applied at
-- API startup by app/core/schema.py.
--
-- Nothing in this file may USE the 'github' enum value: ALTER TYPE ... ADD VALUE
-- runs inside the migration transaction (Postgres 12+), where a freshly added
-- value cannot be referenced until that transaction commits.

ALTER TYPE rag_source ADD VALUE IF NOT EXISTS 'github';

-- The source's own version marker (blob sha for files, updated_at for issues/PRs),
-- used to skip unchanged items before they are enqueued.
ALTER TABLE documents ADD COLUMN IF NOT EXISTS external_version TEXT;

ALTER TABLE oauth_connections ADD COLUMN IF NOT EXISTS needs_reconnect_at TIMESTAMPTZ;
-- Round-robin start position across a connection's repositories between syncs.
ALTER TABLE oauth_connections ADD COLUMN IF NOT EXISTS github_rr_offset INT NOT NULL DEFAULT 0;

-- Repositories a connection can see through the GitHub App, with per-repo sync cursors.
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
);

-- Items that were dead-lettered; the sync skips one until its source version changes.
CREATE TABLE IF NOT EXISTS github_failures (
    connection_id UUID NOT NULL REFERENCES oauth_connections(id) ON DELETE CASCADE,
    external_id TEXT NOT NULL,
    version TEXT NOT NULL,
    attempts INT NOT NULL DEFAULT 1,
    last_error TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (connection_id, external_id)
);

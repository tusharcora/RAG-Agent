-- Multi-tenant organizations + connection-level access control.
-- Runs after 001_init.sql and 002_rag_schema.sql (filename ordering).

CREATE TABLE IF NOT EXISTS organizations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS org_members (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('owner', 'admin', 'member')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_org_members_user ON org_members (user_id);

-- org_members.user_id is NOT NULL, so a pending invite (no user row yet) needs its own table.
CREATE TABLE IF NOT EXISTS org_invites (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('owner', 'admin', 'member')),
    token TEXT NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    accepted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Org-scoped machine credential for /sync automation. Replaces the legacy global
-- API_SHARED_SECRET entirely rather than sitting alongside it.
CREATE TABLE IF NOT EXISTS service_tokens (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE, -- sha256; plaintext shown once at creation, never stored
    label TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at TIMESTAMPTZ
);

-- Allow-list used only when the connection is 'restricted'.
CREATE TABLE IF NOT EXISTS connection_members (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    connection_id UUID NOT NULL REFERENCES oauth_connections(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (connection_id, user_id)
);

-- One connection per provider PER ORG, not system-wide. Default every connection to
-- org-wide visible; an admin flips it to 'restricted' and curates connection_members.
ALTER TABLE oauth_connections ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES organizations(id) ON DELETE CASCADE;
ALTER TABLE oauth_connections ADD COLUMN IF NOT EXISTS visibility_mode TEXT NOT NULL DEFAULT 'org_wide'
    CHECK (visibility_mode IN ('org_wide', 'restricted'));
ALTER TABLE oauth_connections ALTER COLUMN org_id SET NOT NULL;
ALTER TABLE oauth_connections DROP CONSTRAINT IF EXISTS oauth_connections_provider_key;
ALTER TABLE oauth_connections ADD CONSTRAINT oauth_connections_org_provider_key UNIQUE (org_id, provider);

-- Jira issue IDs are small ints local to one Jira site -- two orgs' sites WILL collide on
-- external_id. Scope uniqueness by connection_id (already a column on documents) instead of
-- adding a redundant org_id column: a connection belongs to exactly one org via the
-- constraint above, so this is equivalent org-scoping with one fewer column.
ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_source_external_id_key;
ALTER TABLE documents ADD CONSTRAINT documents_connection_source_external_id_key
    UNIQUE (connection_id, source, external_id);

-- Nullable: only rag.* events populate it; future codereview.* events can leave it null.
ALTER TABLE event_log ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES organizations(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_event_log_org ON event_log (org_id);

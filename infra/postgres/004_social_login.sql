-- Social login (Google/GitHub) as an alternative to email+password. Runs
-- after 001-003 (filename ordering).

-- Social-only accounts never set a password.
ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL;

-- One row per linked provider identity. A user can link both Google and
-- GitHub to the same account (matched by email at link time, see
-- services/api/app/api/routes/social_auth.py), so this is a separate table
-- rather than columns on users.
CREATE TABLE IF NOT EXISTS oauth_identities (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    provider TEXT NOT NULL CHECK (provider IN ('google', 'github')),
    provider_user_id TEXT NOT NULL,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    email TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (provider, provider_user_id)
);
CREATE INDEX IF NOT EXISTS idx_oauth_identities_user ON oauth_identities (user_id);

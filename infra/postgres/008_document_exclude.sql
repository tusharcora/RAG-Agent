-- Lets an org owner/admin stop a single stale/wrong document from being
-- retrieved without disconnecting (or re-syncing) the whole connection.
-- Connection-level visibility_mode (003_orgs_and_access.sql) is too coarse
-- for "this one page is wrong" — this is a document-level kill switch on
-- top of it, checked alongside the existing org/visibility filters in
-- query.py's retrieval SELECT.
ALTER TABLE documents ADD COLUMN excluded_from_retrieval BOOLEAN NOT NULL DEFAULT FALSE;

-- Full-text search for the Knowledge Base browser's search box, which used
-- Document.title.ilike(f"%{search}%") (services/api/app/api/routes/documents.py)
-- with no index support. Title-only v1 (chunk-content indexing is a bigger,
-- separate lift given how large/plentiful chunk rows are per document) --
-- still a real upgrade over ILIKE: ranked, stemmed, indexed.

ALTER TABLE documents ADD COLUMN IF NOT EXISTS search_vector tsvector
    GENERATED ALWAYS AS (to_tsvector('english', title)) STORED;

CREATE INDEX IF NOT EXISTS idx_documents_search_vector ON documents USING gin (search_vector);

-- Incremental sync support. Idempotent: safe to run on an existing database.

-- Where each source's sync stopped. Advanced in the same transaction as the documents it covers.
CREATE TABLE IF NOT EXISTS sync_state (
    source     TEXT PRIMARY KEY,
    cursor     TEXT        NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Hash of a document's content, so a re-fetched record that did not change is skipped and one that did is replaced.
ALTER TABLE documents ADD COLUMN IF NOT EXISTS content_hash TEXT;

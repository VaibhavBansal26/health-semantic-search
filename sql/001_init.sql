CREATE EXTENSION IF NOT EXISTS vector;

-- One row per drug label (a DailyMed "set id" at a given version).
CREATE TABLE documents (
    id            BIGSERIAL PRIMARY KEY,
    source        TEXT        NOT NULL DEFAULT 'dailymed',
    set_id        TEXT        NOT NULL,
    version       INTEGER     NOT NULL,
    doc_type      TEXT,
    title        TEXT        NOT NULL,
    labeler       TEXT,
    effective_date DATE,
    ingested_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source, set_id)
);

-- One row per labeled section (Indications, Dosage, Warnings, ...).
-- Kept whole so a retrieved chunk can be expanded to its parent section.
CREATE TABLE sections (
    id           BIGSERIAL PRIMARY KEY,
    document_id  BIGINT  NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ordinal      INTEGER NOT NULL,
    loinc_code   TEXT,
    title        TEXT    NOT NULL,
    text         TEXT    NOT NULL,
    UNIQUE (document_id, ordinal)
);

-- The retrieval unit: a slice of one section, small enough to embed.
CREATE TABLE chunks (
    id           BIGSERIAL PRIMARY KEY,
    document_id  BIGINT  NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    section_id   BIGINT  NOT NULL REFERENCES sections(id)  ON DELETE CASCADE,
    chunk_index  INTEGER NOT NULL,
    text         TEXT    NOT NULL,
    approx_tokens INTEGER NOT NULL,
    UNIQUE (section_id, chunk_index)
);

CREATE INDEX chunks_document_idx ON chunks (document_id);

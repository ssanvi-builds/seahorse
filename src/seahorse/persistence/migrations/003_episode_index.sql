-- 003_episode_index.sql — episode_index, the bi-temporal bridge table (NO body).
-- Source: f5-06 §3.4.1 + SO-1 (title/summary columns promoted by #8, owned by #6).
-- Cache derivado reconstruible; the indexer repopulates it reading .md via #3.
-- title/summary (SO-1) let the INDEX level serve snippets without hydrating body.
CREATE TABLE IF NOT EXISTS episode_index (
    ep_id            TEXT PRIMARY KEY,
    subject          TEXT,
    fact_id          TEXT,
    valid_at         TEXT,                         -- null-safe (PENDING_INGEST)
    invalid_at       TEXT,
    created_at       TEXT NOT NULL,
    expired_at       TEXT,
    supersedes       TEXT,
    cognitive_type   TEXT,
    source_type      TEXT,                          -- agent | human | importer | system
    schema_version   TEXT NOT NULL,
    skip_extraction  INTEGER NOT NULL DEFAULT 0,   -- ADR-09 flag; 1 = exclude from FTS5 + embedding queue
    file_path        TEXT,                         -- denormalized from episode_paths for convenience
    mtime_ms         INTEGER,                      -- drift detection vs filesystem
    size             INTEGER,                      -- drift detection vs filesystem
    title            TEXT,                         -- SO-1: INDEX-level snippet, no body hydration
    summary          TEXT,                         -- SO-1: INDEX-level snippet, no body hydration
    CHECK (valid_at IS NULL OR invalid_at IS NULL OR valid_at <= invalid_at),
    CHECK (expired_at IS NULL OR created_at <= expired_at)
);

-- I11 mirror: at most one vigente per fact_id in vault-backed mode.
CREATE UNIQUE INDEX IF NOT EXISTS uq_episode_index_active_per_subject
    ON episode_index (fact_id) WHERE invalid_at IS NULL AND expired_at IS NULL;

CREATE INDEX IF NOT EXISTS ix_episode_index_pit_valid
    ON episode_index (valid_at, invalid_at);
CREATE INDEX IF NOT EXISTS ix_episode_index_known
    ON episode_index (created_at, expired_at);
CREATE INDEX IF NOT EXISTS ix_episode_index_supersedes
    ON episode_index (supersedes);
CREATE INDEX IF NOT EXISTS ix_episode_index_subject
    ON episode_index (subject) WHERE invalid_at IS NULL AND expired_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_episode_index_fact_id
    ON episode_index (fact_id);
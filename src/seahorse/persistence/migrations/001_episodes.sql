-- 001_episodes.sql — episodes (contrato #2 §3, operado por #6). Source: f5-06 §3.2.
-- Append-only. subject/fact_id are DERIVED storage columns (Engine computes them).
-- MVP-0: table may stay empty in vault-backed mode; created so the schema
-- survives the switch to DB-backed without a migration.
CREATE TABLE IF NOT EXISTS episodes (
    id              TEXT PRIMARY KEY,             -- F3.1 id (UUIDv7)
    subject         TEXT,                         -- DERIVED (Engine, H1/frontmatter.title normalized)
    fact_id         TEXT,                         -- DERIVED (deterministic hash of subject)
    body_md         TEXT NOT NULL,
    valid_at        TEXT,                         -- ISO-8601 'Z' or NULL (PENDING_INGEST legitimate)
    invalid_at      TEXT,
    created_at      TEXT NOT NULL,
    expired_at      TEXT,
    supersedes      TEXT,
    cognitive_type  TEXT,
    source_type     TEXT,                          -- agent | human | importer | system (SO-4)
    schema_version  TEXT NOT NULL,
    provenance      TEXT NOT NULL,                 -- JSONB in Postgres; TEXT + json_valid in SQLite
    CHECK (valid_at IS NULL OR invalid_at IS NULL OR valid_at <= invalid_at),
    CHECK (expired_at IS NULL OR created_at <= expired_at),
    CHECK (json_valid(provenance))                 -- enforce json-valid in storage, not just app
);
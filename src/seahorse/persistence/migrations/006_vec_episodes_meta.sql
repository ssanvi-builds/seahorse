-- 006_vec_episodes_meta.sql — vec_episodes_meta, the lateral table for the
-- model-identity stamp (SO-7a). Source: f5-07 §4.1, signed SO-7.
-- #6 is model-agnostic and vec0 does not allow arbitrary auxiliary columns, so
-- the stamp lives in this lateral table (soft ref to ep_id, no FK to vec0).
-- MVP-0: table created (DDL ownership is #6 per SO-7); the vec0 virtual table
-- and the row population are deferred to MVP-1 (no sqlite-vec runtime dep).
CREATE TABLE IF NOT EXISTS vec_episodes_meta (
    ep_id            TEXT PRIMARY KEY,           -- soft ref, no FK to vec0 (virtual table)
    model_identity   TEXT NOT NULL,              -- cache_key(): backend:model:rev12:dim:quant
    content_hash     TEXT NOT NULL,              -- sha256(normalized_text|role)
    embedded_at      TEXT NOT NULL,
    dim              INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vec_meta_identity ON vec_episodes_meta(model_identity);
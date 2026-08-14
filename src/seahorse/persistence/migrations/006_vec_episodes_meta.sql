-- 006_vec_episodes_meta.sql — vec_episodes_meta, the lateral table for the
-- model-identity stamp. Persistence is model-agnostic and vec0 does not allow
-- arbitrary auxiliary columns, so the stamp lives in this lateral table (soft
-- ref to ep_id, no FK to vec0). The table is created now; the vec0 virtual
-- table and row population land with the vector index (no sqlite-vec runtime
-- dependency in the default install).
CREATE TABLE IF NOT EXISTS vec_episodes_meta (
    ep_id            TEXT PRIMARY KEY,           -- soft ref, no FK to vec0 (virtual table)
    model_identity   TEXT NOT NULL,              -- cache_key(): backend:model:rev12:dim:quant
    content_hash     TEXT NOT NULL,              -- sha256(normalized_text|role)
    embedded_at      TEXT NOT NULL,
    dim              INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vec_meta_identity ON vec_episodes_meta(model_identity);

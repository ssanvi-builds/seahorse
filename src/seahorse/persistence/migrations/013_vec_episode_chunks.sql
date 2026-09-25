-- 013_vec_episode_chunks.sql — chunk-level vec0 virtual table (P3.2 seam,
-- flag-gated: populated ONLY when the composition root wires the chunked
-- repository — build_facade(chunk_mode="chunked")). Parallel to vec_episodes:
-- one row per CHUNK, chunk_id = "{parent_ep_id}#{i}" (deterministic, ordered),
-- +parent_ep_id for the read-side fold. The sqlite-vec extension MUST be
-- loaded on the connection BEFORE this migration runs (ConnectionManager
-- loads "vec0" in open(); Storage opts in at the composition root).
--
-- Blast radius with the flag OFF (the default): the tables exist but stay
-- EMPTY — every consumer of vec_episodes keeps its 1-row = 1-episode
-- assumption and the flag-off retrieval path is bit-identical.
CREATE VIRTUAL TABLE IF NOT EXISTS vec_episode_chunks USING vec0(
    chunk_id        TEXT PRIMARY KEY,           -- "{parent_ep_id}#{i}" — deterministic
    embedding       float[384],                 -- vec0 rejects NOT NULL on the vector column
    +parent_ep_id   TEXT,                       -- fold chunk -> parent at kNN time
    +fact_id        TEXT,                       -- inherited from the parent's episode_index row
    +invalid_at     TEXT,                       -- inherited (currently-valid pushdown)
    +cognitive_type TEXT,                       -- inherited (cognitive-type pushdown)
    +created_at     TEXT                        -- inherited (bi-temporal windowing)
);

-- Lateral model-identity stamp (the vec_episodes_meta pattern): vec0 does not
-- allow arbitrary auxiliary columns, so the stamp lives here (soft ref to
-- chunk_id, no FK to the virtual table).
CREATE TABLE IF NOT EXISTS vec_chunks_meta (
    chunk_id        TEXT PRIMARY KEY,           -- soft ref, no FK to vec0 (virtual table)
    parent_ep_id    TEXT NOT NULL,
    model_identity  TEXT NOT NULL,              -- cache_key(): backend:model:rev12:dim:quant
    content_hash    TEXT NOT NULL,              -- sha256(chunk_text|passage)
    embedded_at     TEXT NOT NULL,
    dim             INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vec_chunks_meta_identity ON vec_chunks_meta(model_identity);
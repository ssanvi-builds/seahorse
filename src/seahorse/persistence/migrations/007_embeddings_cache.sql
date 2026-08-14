-- 007_embeddings_cache.sql — embeddings_cache, content-hash cache keyed by
-- (content_hash, model_identity, role). Operated by the embedder via the
-- EmbeddingsCacheRepository protocol; no own connection. vector is
-- np.float32.tobytes() (dim*4 bytes).
CREATE TABLE IF NOT EXISTS embeddings_cache (
    content_hash      TEXT NOT NULL,
    model_identity    TEXT NOT NULL,
    role              TEXT NOT NULL,             -- 'query' | 'passage'
    vector            BLOB NOT NULL,             -- np.float32.tobytes(), dim*4 bytes
    dim               INTEGER NOT NULL,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (content_hash, model_identity, role)
);
CREATE INDEX IF NOT EXISTS idx_emb_cache_lookup
    ON embeddings_cache(model_identity, role, content_hash);

-- 010_vec_fts.sql — vec0 virtual table + FTS5 external-content tables (M1-A.2).
-- Source: f5-06 §3.4.3 (vec0) / §3.4.4 (FTS5 external content), signed SO-7.
-- The sqlite-vec extension MUST be loaded on the connection BEFORE this
-- migration runs (``ConnectionManager`` loads "vec0" in ``open()``; ``Storage``
-- opts in at the composition root — M1-A.1). No triggers: the FTS repo emits
-- explicit INSERT / 'delete' / 'rebuild' commands against the virtual tables.
CREATE VIRTUAL TABLE IF NOT EXISTS vec_episodes USING vec0(
    ep_id            TEXT PRIMARY KEY,           -- vector keyed by ep_id (bi-temporal preserved)
    embedding        float[384],                 -- vec0 rejects NOT NULL on the vector column
    +fact_id         TEXT,                       -- filter pushdown in kNN
    +invalid_at      TEXT,                       -- kept in sync with episodes/episode_index (M1-A.5)
    +cognitive_type  TEXT,                       -- pre-filter in kNN (G1 pushdown)
    +created_at      TEXT                        -- bi-temporal windowing
);

-- FTS5 external-content table. Column order MUST match episode_fts (minus the
-- rowid) so FTS5's positional content mapping stays aligned.
CREATE TABLE IF NOT EXISTS episode_content (
    rowid       INTEGER PRIMARY KEY,
    ep_id       TEXT NOT NULL,
    body_md     TEXT NOT NULL,
    title       TEXT,
    tags        TEXT,                            -- ' '.join(tags)
    summary     TEXT,
    subject     TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_episode_content_ep_id ON episode_content (ep_id);

CREATE VIRTUAL TABLE IF NOT EXISTS episode_fts USING fts5(
    ep_id UNINDEXED, body_md, title, tags, summary, subject UNINDEXED,
    content='episode_content',
    content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);

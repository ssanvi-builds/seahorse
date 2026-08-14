-- 010_vec_fts.sql — vec0 virtual table + FTS5 external-content tables.
-- The sqlite-vec extension MUST be loaded on the connection BEFORE this
-- migration runs (ConnectionManager loads "vec0" in open(); Storage opts in at
-- the composition root). No triggers: the FTS repository emits explicit INSERT
-- / 'delete' / 'rebuild' commands against the virtual tables.
CREATE VIRTUAL TABLE IF NOT EXISTS vec_episodes USING vec0(
    ep_id            TEXT PRIMARY KEY,           -- vector keyed by ep_id (bi-temporal preserved)
    embedding        float[384],                 -- vec0 rejects NOT NULL on the vector column
    +fact_id         TEXT,                       -- filter pushdown in kNN
    +invalid_at      TEXT,                       -- kept in sync with episodes/episode_index
    +cognitive_type  TEXT,                       -- pre-filter in kNN (cognitive-type pushdown)
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

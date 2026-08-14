-- 004_episode_paths.sql — episode_paths, the mutable sidecar of the frontmatter
-- adapter. file_path lives in a separate mutable table (not in append-only
-- episodes) because a file rename requires UPDATE. set_path is an UPDATE over
-- this table; the cache is derived and rebuildable.
CREATE TABLE IF NOT EXISTS episode_paths (
    ep_id       TEXT PRIMARY KEY,
    file_path   TEXT NOT NULL,
    mtime_ms    INTEGER NOT NULL,                 -- drift detection (medium-term: etag/hash)
    size        INTEGER NOT NULL,
    CHECK (file_path LIKE '%.md')
);

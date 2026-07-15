-- 004_episode_paths.sql — episode_paths, the mutable sidecar of #3.
-- Source: f5-06 §3.4.2. file_path lives in a SEPARATE mutable table (not in
-- append-only episodes) because a file rename requires UPDATE. set_path is
-- UPDATE over this table; cache derivado reconstruible.
CREATE TABLE IF NOT EXISTS episode_paths (
    ep_id       TEXT PRIMARY KEY,
    file_path   TEXT NOT NULL,
    mtime_ms    INTEGER NOT NULL,                 -- drift detection (mediano: etag/hash)
    size        INTEGER NOT NULL,
    CHECK (file_path LIKE '%.md')
);
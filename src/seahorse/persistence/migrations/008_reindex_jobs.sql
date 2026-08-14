-- 008_reindex_jobs.sql — reindex_jobs, resumable backfill job state.
-- Operated by the embedder and the CLI via ReindexJobRepository. Setters only
-- (no state-transition guards): create/start/pause/finish/fail/list.
CREATE TABLE IF NOT EXISTS reindex_jobs (
    job_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    model_from    TEXT NOT NULL,
    model_to      TEXT NOT NULL,
    total         INTEGER NOT NULL,
    done          INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL,                 -- 'running' | 'paused' | 'done' | 'failed'
    started_at    TEXT NOT NULL,
    finished_at   TEXT
);

-- 008_reindex_jobs.sql — reindex_jobs, resumable backfill job state (SO-7a).
-- Source: f5-07 §5.4, signed SO-7. Operated by #7/#14 via ReindexJobRepository.
-- MVP-0: setters (no state-transition guards); create/start/pause/finish/fail/list.
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
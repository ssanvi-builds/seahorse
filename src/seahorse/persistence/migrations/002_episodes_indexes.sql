-- 002_episodes_indexes.sql — the engine's indexes plus one persistence addition.
-- Partial unique indexes make re-invalidation and duplicate current-state rows
-- detectable as IntegrityError (null-safe, unique-current).
-- invalid_at is set once; the partial unique index makes re-invalidation detectable.
CREATE UNIQUE INDEX IF NOT EXISTS uq_episodes_id_invalid_once
    ON episodes (id) WHERE invalid_at IS NOT NULL;

-- At most one current row (including pending-ingest) per fact_id.
CREATE UNIQUE INDEX IF NOT EXISTS uq_one_active_per_subject
    ON episodes (fact_id) WHERE invalid_at IS NULL AND expired_at IS NULL;

-- Point-in-time queries with a variable t: full index (no partial — verdict A).
-- A partial index with a constant predicate (WHERE valid_at <= :t) does not
-- parameterize t.
CREATE INDEX IF NOT EXISTS ix_episodes_pit_valid
    ON episodes (valid_at, invalid_at);

-- Known-at point-in-time (system axis).
CREATE INDEX IF NOT EXISTS ix_episodes_known
    ON episodes (created_at, expired_at);

-- Supersedes chain (chain_from).
CREATE INDEX IF NOT EXISTS ix_episodes_supersedes
    ON episodes (supersedes);

-- Hot path get_vigente / detect_collisions (partial, current).
CREATE INDEX IF NOT EXISTS ix_episodes_subject
    ON episodes (subject) WHERE invalid_at IS NULL AND expired_at IS NULL;

-- Additional: fact_id lookup for find_vigent_by_fact_id and chain_from.
CREATE INDEX IF NOT EXISTS ix_episodes_fact_id
    ON episodes (fact_id);

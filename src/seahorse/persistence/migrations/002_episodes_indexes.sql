-- 002_episodes_indexes.sql — the 6 contract #2 indexes + 1 #6 addition.
-- Source: f5-06 §3.3. Partial unique indexes make re-invalidation and duplicate
-- vigente detectable as IntegrityError (I5 null-safe, I11 unique-vigente).
-- I3/I6: invalid_at set once. Partial unique makes re-invalidation detectable.
CREATE UNIQUE INDEX IF NOT EXISTS uq_episodes_id_invalid_once
    ON episodes (id) WHERE invalid_at IS NOT NULL;

-- I11: at most one VIGENTE (incluye PENDING_INGEST) per fact_id.
CREATE UNIQUE INDEX IF NOT EXISTS uq_one_active_per_subject
    ON episodes (fact_id) WHERE invalid_at IS NULL AND expired_at IS NULL;

-- PIT queries con t variable: FULL index (no partial — fix veredicto A #2).
-- A partial index con predicado constante (WHERE valid_at <= :t) NO parameteriza t.
CREATE INDEX IF NOT EXISTS ix_episodes_pit_valid
    ON episodes (valid_at, invalid_at);

-- Known-at PIT (eje sistema).
CREATE INDEX IF NOT EXISTS ix_episodes_known
    ON episodes (created_at, expired_at);

-- Cadena supersedes (chain_from).
CREATE INDEX IF NOT EXISTS ix_episodes_supersedes
    ON episodes (supersedes);

-- Hot path get_vigente / detect_collisions (partial, vigente).
CREATE INDEX IF NOT EXISTS ix_episodes_subject
    ON episodes (subject) WHERE invalid_at IS NULL AND expired_at IS NULL;

-- ADICIONAL #6: lookup por fact_id para find_vigent_by_fact_id y chain_from sobre fact_id.
CREATE INDEX IF NOT EXISTS ix_episodes_fact_id
    ON episodes (fact_id);
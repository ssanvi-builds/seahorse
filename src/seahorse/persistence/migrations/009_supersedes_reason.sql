-- 009_supersedes_reason.sql — portable supersedes_reason column lands in storage
-- (f5-03 §12.3, plan 5.3=(a)).
--
-- Adds supersedes_reason TEXT to episodes + episode_index. The Episode model has
-- carried this field since commit 1 (model+wire only, not yet persisted); commit 4
-- persists it and wires it into _EPISODES_INSERT/_INDEX_INSERT/_row_to_episode +
-- the sidecar rebuild_all episode_index upsert.
--
-- Idempotency: SQLite ALTER TABLE ADD COLUMN has no IF NOT EXISTS and cannot be
-- made conditional in raw SQL (executescript is non-procedural). The PRIMARY
-- idempotency mechanism is the migration runner's schema_version row (each NNN
-- runs at most once per DB). The BEGIN/COMMIT wrapper below makes the two ALTERs
-- atomic WITH EACH OTHER: if either fails, neither commits, the runner never
-- inserts version=9, and re-running retries both. (001-008 use
-- CREATE TABLE/INDEX IF NOT EXISTS as defense-in-depth; ALTER cannot, so the
-- runner is the sole guard here — consistent with how 001-008 idempotency truly
-- works, since CREATE...IF NOT EXISTS alone does not insert the version row.)
-- Residual gap (documented, out of commit 4 scope): if both ALTERs commit but the
-- runner's subsequent INSERT INTO schema_version fails, re-running would raise
-- "duplicate column name: supersedes_reason". A full fix wraps the runner itself
-- in a single tx — deferred to a separate hygiene commit.
--
-- No CHECK: the field is a free TEXT enum string
-- (contradiction/correction/merge/revalidation/decay); enum enforcement is at
-- the Pydantic model (SupersedesReason in frontmatter/schema.py), not storage.
BEGIN;
ALTER TABLE episodes ADD COLUMN supersedes_reason TEXT;
ALTER TABLE episode_index ADD COLUMN supersedes_reason TEXT;
COMMIT;
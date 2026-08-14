-- 005_audit_events.sql — audit_events, append-only observability (not derivable
-- from markdown). The AuditEvent type is defined by the engine; persistence only
-- serializes it to a row. The id column (autoincrement PK) is not part of the
-- AuditEvent type — it is storage-generated.
CREATE TABLE IF NOT EXISTS audit_events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id         TEXT,
    session_id       TEXT,
    primitive        TEXT NOT NULL,            -- apply|forget|improve|revalidate|decay|rebuild
    target_id        TEXT,                     -- ep_id target
    successor_id     TEXT,                     -- for improve/revalidate
    valid_time       TEXT,                     -- snapshot of the target's valid_at
    transaction_time TEXT NOT NULL,           -- engine now()
    reason           TEXT,
    cognitive_type   TEXT,
    result           TEXT                      -- added|updated|invalidated|decayed
);
CREATE INDEX IF NOT EXISTS ix_audit_target  ON audit_events (target_id, transaction_time);
CREATE INDEX IF NOT EXISTS ix_audit_session ON audit_events (session_id, transaction_time);

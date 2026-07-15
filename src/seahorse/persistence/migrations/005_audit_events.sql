-- 005_audit_events.sql — audit_events, append-only observability (NOT derivable
-- from markdown). Source: f5-06 §3.4.5. AuditEvent type is defined by #2; #6
-- only serializes it to a row. The id column (autoincrement PK) is NOT part of
-- the AuditEvent type — it is storage-generated.
CREATE TABLE IF NOT EXISTS audit_events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id         TEXT,
    session_id       TEXT,
    primitive        TEXT NOT NULL,            -- apply|forget|improve|revalidate|decay|rebuild
    target_id        TEXT,                     -- ep_id objetivo
    successor_id     TEXT,                     -- para improve/revalidate
    valid_time       TEXT,                     -- snapshot valid_at del target
    transaction_time TEXT NOT NULL,           -- now() del Engine
    reason           TEXT,
    cognitive_type   TEXT,
    result           TEXT                      -- added|updated|invalidated|decayed
);
CREATE INDEX IF NOT EXISTS ix_audit_target  ON audit_events (target_id, transaction_time);
CREATE INDEX IF NOT EXISTS ix_audit_session ON audit_events (session_id, transaction_time);
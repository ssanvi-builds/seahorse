---
id: 01a05c35-fdbe-702b-88fb-6c4033fdfd95
created_at: '2026-09-01T09:03:57.374378Z'
schema_version: 1.0.0
provenance:
  agent_id: consolidator
  confidence: 0.92
  extraction_mode: consolidated
  model_used: claude-sonnet-5
  prompt_hash: 2920a4696a27c5d733aff70bf0c2932f5b3dc1a0799a2ed3db22c7094ac9edf0
  session_id: consolidate-01a05c35-fdbe-7f74-b702-55fa78c53235
  source_type: system
supersedes: 01a023c6-2129-7b33-899d-1224843804a4
supersedes_reason: merge
cognitive_type: semantic
source_type: system
title: ember token refresh outage
summary: "The 2026-08-19 refresh outage was a dropped per-family lock after a\ndeploy; recovery force-refreshed the affected families and the deploy\nguard now asserts the lock's presence."
tags: []
---
# ember token refresh outage

The 2026-08-19 refresh outage was a dropped per-family lock after a
deploy; recovery force-refreshed the affected families and the deploy
guard now asserts the lock's presence.

## Context

The morning deploy on 2026-08-19 shipped a refresh-path refactor that
silently dropped the per-family serialization: two refreshes on one
token family invalidated each other, and every client retried in a
loop. The outage ran three sessions: detection and triage on the 19th,
mitigation on the 20th, retro on the 21st. [[Noah Bennett]] correlated
the client-side symptoms within minutes.

## Timeline

- **09:03 UTC** — Support page: refresh loops, 401s climbing.
- **09:40 UTC** — Triage: the deploy config shows
  `per_family_lock: false` — a regression, not a client bug.
- **08-20** — Mitigation: serializer rolled back, lock restored, 340
  affected families force-refreshed.
- **08-21** — Retro: deploy-guard assertion, refresh-loop alarm,
  client-side backoff cap.

## Options considered

- **Client-side retry fix only.** Rejected: the loop is server-caused;
  a client patch masks the next regression of the same class.
- **Lock restored, nothing else.** Rejected: nothing would catch the
  next deploy that drops it — the guard must assert the invariant.
- **Lock restored + deploy-guard assertion + loop alarm + backoff cap.**
  Defense at the server, detection in the metrics, kindness at the
  client.

## Decision

The per-family lock is asserted by the deploy guard (a build failure if
absent), a refresh-loop alarm watches the audit metrics, and the mobile
clients cap refresh backoff (agreed with [[Noah Bennett]]).

## Consequences

- All 340 affected families recovered without re-login; no data loss,
  4 hours sev-2.
- The outage validated the rotation-v2 path: the rota inherited the
  open sev across the Monday handover without a drop.
- [[Sam Whitaker]] added the lock assertion to the security checklist
  for every Ember auth-path change.

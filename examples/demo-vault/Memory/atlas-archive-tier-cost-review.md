---
id: 01a08fb3-2abe-7cd1-baef-000f884aaa43
created_at: '2026-09-11T09:01:18.910451Z'
schema_version: 1.0.0
provenance:
  agent_id: consolidator
  confidence: 0.89
  extraction_mode: consolidated
  model_used: claude-sonnet-5
  prompt_hash: cf416f173099ec45599f0b214e445ab8fa4d8d8f34d370cfd15559843674f205
  session_id: consolidate-01a08fb3-2abe-792a-b7f7-8d7148d21c2a
  source_type: system
supersedes: 01a0856b-2a21-76e2-8bc2-481b8d7560a8
supersedes_reason: merge
cognitive_type: semantic
source_type: system
title: atlas archive tier cost review
summary: "The archive tier stays quarterly, with the audit window moved to warm\nstorage and a retrieve budget alert — the September sync's open item,\nclosed with numbers."
tags: []
---
# atlas archive tier cost review

The archive tier stays quarterly, with the audit window moved to warm
storage and a retrieve budget alert — the September sync's open item,
closed with numbers.

## Context

Two quarters of partitions sit in the Glacier vaults and the weekly
sync committed to a cost review in September. The review ran across
three sessions: the bill shape, the retrieve-pattern breakdown, and the
proposal the sync will sign off.

## Options considered

- **Everything to deep archive.** Rejected: restores are already the
  slow path (p95 11h); deep archive doubles it for a saving the audit
  pattern does not need.
- **Drop the archive tier entirely.** Rejected: the replay tool and the
  dedup audits depend on older offsets; the retention discussion
  settled that two quarters ago.
- **Quarterly vaults + warm audit window + retrieve budget alert.**
  Audits read warm (fast, predictable), everything else stays cold,
  and a runaway retrieve pattern pages before the bill does.

## Decision

Keep the quarterly vault layout; the last quarter lives in warm
storage for the audit pattern; retrieves carry a budget alert in
`repos/atlas/config/archive.yaml`.

## Consequences

- The retrieve bill concentrates where the audits actually read —
  the warm window covers 80% of retrieves at access-latency cost.
- The budget alert closes the "guesswork" part of the bill shape.
- [[Tomas Rivera]] signs the Q4 capacity against these numbers at the
  September sync; [[Daniel Okafor]] owns the runbook update.

---
id: 019d6c5f-0639-794f-9ffd-1c850aa02b04
created_at: '2026-04-08T09:14:20.089999Z'
schema_version: 1.0.0
provenance:
  agent_id: consolidator
  confidence: 0.88
  extraction_mode: consolidated
  model_used: claude-sonnet-5
  prompt_hash: 8123b49f46b8e609fe4dc883d2912308e1fae0a88de6014fe370923d4bc45268
  session_id: consolidate-019d6c5f-0639-714b-93a1-71047a174561
  source_type: system
supersedes: 019d1f45-e7a8-7150-8049-cbf568d24545
supersedes_reason: merge
cognitive_type: semantic
source_type: system
title: atlas retention policy discussion
summary: Atlas keeps 21 days of hot Kafka data and moves older partitions to a quarterly archive tier.
tags: []
---
# atlas retention policy discussion

Atlas keeps 21 days of hot Kafka data and moves older partitions to a quarterly archive tier.

## Context

The original 30-day flat retention filled the lakehouse budget two quarters
running, and the budget review made it clear the next sign-off (Tomas) would
not fund the same shape for Q3. The discussion ran across three sessions:
the storage review that opened it, the budget decision that cut hot storage
to 21 days, and the wrap-up that had to answer what happens to the older
partitions.

## Options considered

- **Keep 30 days flat.** Rejected: the storage trend line does not bend, and
  no one could name a consumer that actually reads past three weeks.
- **Cut to 21 days hot, drop the rest.** Rejected by [[Daniel Okafor]]: the
  replay tool needs older offsets for backfill windows, and the orders
  dedup checks replay from the archive.
- **21 days hot + quarterly archive tier.** Keeps every consumer's working
  set on hot storage while backfills and audits read the archive tier at
  access-latency cost.

## Decision

21 days hot retention on all Atlas topics; partitions older than 21 days
move to the quarterly archive tier. The replay tool accepts archive offsets
(`atlas-replay --from` documents both forms).

## Consequences

- Storage spend on the hot tier drops ~38% against the flat-30 shape.
- Backfills that need data older than 21 days read the archive tier —
  slower, so runbooks say to prefer hot offsets when they exist.
- The orders topic is unaffected on the hot path (dedup reads stay in-window).
- `retention.yaml` is the single source of truth; the runbook links to it.

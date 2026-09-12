---
id: 019e82a2-2388-79e0-b8a7-83b41f883fc6
created_at: '2026-06-01T10:02:04.552560Z'
schema_version: 1.0.0
provenance:
  agent_id: consolidator
  confidence: 0.88
  extraction_mode: consolidated
  model_used: claude-sonnet-5
  prompt_hash: 875cd5907bdcc0ad2b7342fb6099d492ba1e257704af199fefa91c1eabdb372a
  session_id: consolidate-019e82a2-2388-774c-ab4e-4707cbda8634
  source_type: system
supersedes: 019e3ab2-74c7-7183-848e-f4696bce26db
supersedes_reason: merge
cognitive_type: semantic
source_type: system
title: cinder feature backfill tooling
summary: "Cinder backfills run as a proper Feast job (PR 267) with dry-run\npoint-in-time join reports — the hand-run scripts are retired."
tags: []
---
# cinder feature backfill tooling

Cinder backfills run as a proper Feast job (PR 267) with dry-run
point-in-time join reports — the hand-run scripts are retired.

## Context

Nine Q1 feature views needed a backfill before their training cutoffs,
and the hand-run scripts silently leaked future rows into past windows —
not point-in-time-correct, which is disqualifying for training data.
The work ran across three sessions: the staleness audit, the job plan,
and the merged result.

## Options considered

- **Fix the hand-run scripts.** Rejected: they have no join reports and
  no way to prove correctness after the fact.
- **Backfill inside the nightly training job.** Rejected: couples the
  training SLA to backfill latency; a big backfill would starve the
  nightly run.
- **A dedicated Feast backfill job with dry-run reports.** Point-in-time
  joins validated against training cutoffs before any write lands.

## Decision

The backfill job (PR 267, approved by [[Priya Nair]]) runs on demand:
dry-run report first, real run after the joins validate. The runbook is
"How to backfill Cinder features".

## Consequences

- Q1 features backfilled with validated joins (status note 2026-05-21).
- Backfills no longer touch the training job, so the 03:30 UTC window
  SLA holds regardless of backfill load.
- The registry-write serialization fix came out of the same work —
  concurrent backfills no longer contend on the Feast registry lock.

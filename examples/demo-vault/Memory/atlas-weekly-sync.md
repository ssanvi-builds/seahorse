---
id: 01a05711-831c-79eb-a3ff-9814eef82580
created_at: '2026-08-31T09:06:00.604585Z'
schema_version: 1.0.0
provenance:
  agent_id: consolidator
  confidence: 0.92
  extraction_mode: consolidated
  model_used: claude-sonnet-5
  prompt_hash: 90814aa4a9fcf21309802d494ffe99df1b5c5b02854a426f763568ac05b273cf
  session_id: consolidate-01a05711-831c-7a25-86b7-4fb679c1db3c
  source_type: system
supersedes: 01a00f03-6247-794b-8bf4-7e29f165b279
supersedes_reason: merge
cognitive_type: semantic
source_type: system
title: atlas weekly sync
summary: 'Recurring state of Atlas: 40k msg/s sustained ingest, 21-day hot retention with a quarterly archive tier, static consumer membership, and the replay tool in production.'
tags: []
---
# atlas weekly sync

Recurring state of Atlas: 40k msg/s sustained ingest, 21-day hot retention with a quarterly archive tier, static consumer membership, and the replay tool in production.

## Context

The Atlas weekly sync is the standing session where throughput, retention,
and incident follow-ups get reviewed. Three turns of it are captured here —
March, June, and August — and this note is the distilled state across
them, not minutes of any single meeting.

## State by topic

- **Throughput.** The baseline moved from ~31k msg/s (March) to 40k+ after
  the exactly-once tuning and the partition re-balance; the current chain
  records 40k as the number to defend.
- **Retention.** 21 days hot plus the quarterly archive tier (the retention
  discussion note has the full decision); the June sync signed off the Q3
  capacity against it.
- **Consumers.** Static group membership since the July incident; the sync
  tracks extending the deploy guard to Beacon as an open item.
- **Replay tool.** Shipped (2026-04-02) and documented in the runbook, with
  archive offsets supported since the retention change.

## Decisions and commitments

- 40k msg/s is the sustained baseline for capacity planning — regressions
  are incidents, not tuning opportunities.
- The archive tier cost review lands at the September sync.

## Open items

- Extend the deploy guard to Beacon's consumer group.
- Archive-offset replay for lag backfills (owner: [[Daniel Okafor]]).
- Capacity review for Q3 ingestion growth (owner: [[Tomas Rivera]]).

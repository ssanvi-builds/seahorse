---
id: 019e879f-ce22-727f-961b-6dcce74543a3
created_at: '2026-06-02T09:17:37.698160Z'
schema_version: 1.0.0
provenance:
  agent_id: consolidator
  confidence: 0.9
  extraction_mode: consolidated
  model_used: claude-sonnet-5
  prompt_hash: b0cf3415146fccb8f6f57da89523534dc8150140ef0579bdc9bbc482eab80117
  session_id: consolidate-019e879f-ce22-7a69-a6c1-5f21546fbd10
  source_type: system
supersedes: 019e3fa8-4fb6-78c5-aa99-9db46e33aca2
supersedes_reason: merge
cognitive_type: semantic
source_type: system
title: cinder training window
summary: Cinder trains nightly at 03:30 UTC, clear of the EU batch window it used to collide with.
tags: []
---
# cinder training window

Cinder trains nightly at 03:30 UTC, clear of the EU batch window it used to collide with.

## Context

The original 02:00 UTC training window collided with the EU batch window
(02:00–03:10 UTC), and the contention degraded feature freshness for the
EU-morning dashboards — the exact consumers [[Cinder]] exists to serve. The
review ran across three sessions: the collision report, the candidate
windows, and the pick.

## Options considered

- **Stay at 02:00, throttle the batch.** Rejected: the batch is the
  billing-critical path; throttling it moved the problem instead of solving
  it.
- **Move to 03:00 UTC.** Rejected: the EU batch ends 03:10, so the tail
  overlap remained.
- **Move to 03:30 UTC.** Clears the batch end with margin, training still
  finishes before EU morning.

## Decision

Training runs at 03:30 UTC nightly ([[Priya Nair]] owns the window). The
schedule lives in
`repos/cinder/config/schedule.yaml`, and the batch planner output is the
source of truth for the window boundary.

## Consequences

- EU batch untouched (billing path keeps its window).
- Training SLA holds: 30 consecutive green nights as of 2026-07-08.
- EU-morning dashboards read features trained the same night.
- The window is now part of the on-call handover checklist when a training
  overruns — see the runbook.

---
id: 019e164f-dbeb-77c0-a827-a2dff4b30067
created_at: '2026-05-11T09:13:12.939693Z'
schema_version: 1.0.0
provenance:
  agent_id: consolidator
  confidence: 0.92
  extraction_mode: consolidated
  model_used: claude-sonnet-5
  prompt_hash: 41b9abac2e4ee33852eff74b561314bf49e727db0c743fecc541bcdf90a3aabf
  session_id: consolidate-019e164f-dbeb-783e-b8a0-48f3fe7a44f1
  source_type: system
supersedes: 019dce3c-1f46-79c8-aa0d-c73b4ae5c059
supersedes_reason: merge
cognitive_type: semantic
source_type: system
title: on-call rotation overhaul
summary: "On-call rotation v2 runs one calendar owned by [[Iris Kovac]], with\n[[Beacon]] acks syncing to the rota so handovers inherit open sevs."
tags: []
---
# on-call rotation overhaul

On-call rotation v2 runs one calendar owned by [[Iris Kovac]], with
[[Beacon]] acks syncing to the rota so handovers inherit open sevs.

## Context

The pre-v2 rotation was a set of ad-hoc swaps: 12 handovers in 90 days
dropped 4 open sevs because nothing tied an acknowledged alert to the
person taking over. The overhaul ran across three sessions: the gap
report, the single-calendar proposal, and the first live week.

## Options considered

- **Keep ad-hoc swaps, add a reminder bot.** Rejected: reminders do not
  transfer ownership; the dropped sevs were ownership gaps, not memory
  gaps.
- **Two calendars (weekday/weekend).** Rejected: the boundary is where
  sevs go to die; one calendar is auditable.
- **One rota calendar + ack sync + written handover.** [[Iris Kovac]]
  owns the calendar, [[Beacon]] acks update it, and the checklist makes
  the handover a readable artifact instead of a hallway chat.

## Decision

Rotation v2: a single rota calendar, acks sync from Beacon, handovers
follow the written checklist. Config lives in
`repos/beacon/config/rota.yaml`.

## Consequences

- Zero dropped sevs across the first weeks (status note 2026-05-18).
- Mid-week swaps must land in the rota before the swap — the runbook
  makes the ordering explicit.
- The weekly sync tracks extending the same ack-sync pattern to the
  [[Drift]] on-call as an open item.

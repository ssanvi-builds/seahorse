---
id: 019fb248-1184-76c3-8a2c-e6acd9c65cc4
created_at: '2026-07-30T09:08:15.364947Z'
schema_version: 1.0.0
provenance:
  agent_id: consolidator
  confidence: 0.91
  extraction_mode: consolidated
  model_used: claude-sonnet-5
  prompt_hash: 485a451f08f4ffd6a8971ee080af2cfc8f7eec5c43e9a7122041fd9b5562250e
  session_id: consolidate-019fb248-1184-7424-93a1-7d10314ecb50
  source_type: system
supersedes: 019f6a64-5c44-7314-a0b4-a23870cd674e
supersedes_reason: merge
cognitive_type: semantic
source_type: system
title: 'incident review: atlas consumer lag'
summary: "The 2026-07-14 consumer-lag incident was a rebalance storm triggered by a\ndeploy; static group membership and a deploy guard prevent recurrence."
tags: []
---
# incident review: atlas consumer lag

The 2026-07-14 consumer-lag incident was a rebalance storm triggered by a
deploy; static group membership and a deploy guard prevent recurrence.

## Context

During the 2026-07-14 deploy, the orders consumer group rebalanced 47 times
in an hour and p99 lag climbed past 40 seconds on the orders topic. The
incident ran three sessions: detection and triage on the 14th, mitigation on
the 15th, retro on the 16th. Beacon's lag alert fired first (06:12 UTC).

## Timeline

- **06:12 UTC** — Beacon sev-2 lag alert on orders-ingest.
- **06:40 UTC** — Triage: every rebalance in the window correlated with a
  consumer restart from the deploy.
- **14:20 UTC** — Mitigation staged: static group membership for
  orders-ingest, rolling restart to apply it.
- **15:05 UTC** — Verification: p99 lag back under 2s, zero rebalances.
- **07-16** — Retro: action items and the deploy guard.

## Options considered

- **Pause deploys during peak windows.** Rejected: moves the failure, does
  not fix it — the storm would just happen at 02:00.
- **Sticky assignors.** Rejected: still rebalance, just more politely; the
  lag spikes survived the experiment.
- **Static group membership + a deploy guard.** Consumers keep their
  assignments across restarts; the guard blocks deploys that restart the
  group without the static flag set.

## Decision

Static group membership for Atlas consumers (decision recorded 2026-07-21)
plus the deploy guard in the rollback runbook. The config lives in
`repos/atlas/config/consumers.yaml`.

## Consequences

- Zero rebalances across the two deploys since; p99 lag stays under 2s
  (status note 2026-08-04).
- Deploys that touch consumers require the static flag — the guard makes
  forgetting it a build failure, not an incident.
- Follow-ups tracked in the weekly sync: extend the guard to Beacon's
  consumer, and document archive-offset replay for lag backfills.

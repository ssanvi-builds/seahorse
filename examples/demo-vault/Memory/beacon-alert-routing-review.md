---
id: 019daf51-7409-700d-a769-a1ea05777697
created_at: '2026-04-21T09:14:04.169086Z'
schema_version: 1.0.0
provenance:
  agent_id: consolidator
  confidence: 0.91
  extraction_mode: consolidated
  model_used: claude-sonnet-5
  prompt_hash: eb1844731cfb500dfe35b29303f24611cf32adeac91f6a2ed63474c27eb7dc0c
  session_id: consolidate-019daf51-7409-7620-8944-5d37c101415a
  source_type: system
supersedes: 019d675b-4f62-7543-afa6-9b585c73c80e
supersedes_reason: merge
cognitive_type: semantic
source_type: system
title: beacon alert routing review
summary: "Beacon routes sev-1 and sev-2 to PagerDuty and sev-3 to Slack, with alert\nacks synced to the on-call rota."
tags: []
---
# beacon alert routing review

Beacon routes sev-1 and sev-2 to PagerDuty and sev-3 to Slack, with alert
acks synced to the on-call rota.

## Context

Slack-only routing meant every alert landed in the same channel: 812 alerts
in one week, 41% acknowledged within five minutes, and the sev pages that
mattered were getting scrolled past. The review ran across three sessions:
the noise report, the routing proposal, and the tuning pass after the first
week.

## Options considered

- **Everything to PagerDuty.** Rejected: the false-positive rate would have
  burned the rota within a sprint.
- **Keep Slack, add keyword filters.** Rejected: filtering is invisible to
  the on-call and drifts; the routing decision belongs in config.
- **Split by severity, acks sync to the rota.** PagerDuty carries what
  pages, Slack carries what informs, and handovers stop dropping sevs.

## Decision

sev-1 and sev-2 route to PagerDuty; sev-3 stays in Slack. Severity
definitions live in `repos/beacon/config/routing.yaml`. Acknowledging an
alert updates the on-call rota (the decision recorded 2026-05-14).

## Consequences

- First-week numbers: total alerts 812 → 487 (noise −40%), acked-within-5m
  41% → 83%.
- sev-3 alerts in Slack carry an explicit expectation: acknowledged within
  the working day, not the 5-minute page window.
- The rota sync means handovers inherit open sevs — no "lost page" retro
  since it shipped.

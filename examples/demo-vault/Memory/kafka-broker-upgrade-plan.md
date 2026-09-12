---
id: 019f5ac7-08db-76bf-a91d-f87104b3ccd8
created_at: '2026-07-13T09:20:21.211977Z'
schema_version: 1.0.0
provenance:
  agent_id: consolidator
  confidence: 0.9
  extraction_mode: consolidated
  model_used: claude-sonnet-5
  prompt_hash: 59804de45d811c04fb888e77582f409135eb0631ea6b95b33a780c20f0b4e5a9
  session_id: consolidate-019f5ac7-08db-7127-bf8f-f4c883d02835
  source_type: system
supersedes: 019f36d4-c3a3-7866-ae77-0754010fc1d0
supersedes_reason: merge
cognitive_type: semantic
source_type: system
title: kafka broker upgrade plan
summary: "Atlas brokers upgraded 3.6 → 3.8 across two weekend windows with zero\nrebalances and flat p99 lag."
tags: []
---
# kafka broker upgrade plan

Atlas brokers upgraded 3.6 → 3.8 across two weekend windows with zero
rebalances and flat p99 lag.

## Context

The broker fleet sat on 3.6.2 with support ending in September, and the
tiered-storage features the retention discussion wanted need 3.8+. The
plan ran across three sessions: the version audit, the two-window plan,
and the completion check.

## Options considered

- **One big-bang window.** Rejected: a 12-broker fleet does not roll
  back in one night, and the blast radius is the whole ingest path.
- **Upgrade in place, live.** Rejected: controller elections during
  peak ingest are exactly the failure mode the lag incident taught us
  to avoid.
- **Two weekend windows, pinned assignments, per-broker rollback image.**
  Consumer assignments stayed pinned across restarts (the sticky
  assignor pilot held the groups steady) and every broker had a staged
  rollback image, so a bad window rolls back per-node, not fleet-wide.

## Decision

Upgrade brokers to 3.8 in two weekend windows, prerequisite: consumer
assignments pinned, per-broker rollback image staged. The runbook lives
in `repos/atlas/runbooks/broker-upgrade.md`.

## Consequences

- Zero rebalances, p99 lag flat across both windows (status note
  2026-07-06).
- Tiered storage stays off for now — the archive tier decision covers
  the need without the new operational surface.
- The upgrade runbook is the template for the next fleet change;
  [[Daniel Okafor]] owns it.

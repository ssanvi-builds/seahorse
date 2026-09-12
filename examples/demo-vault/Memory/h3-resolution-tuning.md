---
id: 019ebb42-36e4-79b8-9cdb-92c473049aba
created_at: '2026-06-12T09:55:39.364340Z'
schema_version: 1.0.0
provenance:
  agent_id: consolidator
  confidence: 0.91
  extraction_mode: consolidated
  model_used: claude-sonnet-5
  prompt_hash: a6f9d42ea0fba7e5c469e63314d596538116347272aacdac5ed894b3cce390f3
  session_id: consolidate-019ebb42-36e4-77de-bfd2-3545d988f2cf
  source_type: system
supersedes: 019ea6d2-7a32-7151-950b-021cd0bf1c89
supersedes_reason: merge
cognitive_type: semantic
source_type: system
title: h3 resolution tuning
summary: "Beacon geo-alerts standardize on H3 resolution 7; resolution 9 remains\nan exception for the dense urban pilots."
tags: []
---
# h3 resolution tuning

Beacon geo-alerts standardize on H3 resolution 7; resolution 9 remains
an exception for the dense urban pilots.

## Context

The H3 migration fixed border blur, but the pilot regions ran
resolution 9 and the cell count exploded the alert evaluator (p95
9.4s). The tuning ran across three sessions: the runtime report, the
resolution comparison, and the standardization.

## Options considered

- **Resolution 9 everywhere.** Rejected: evaluator runtime scales with
  cell count; 41k cells per evaluation is not sustainable.
- **Resolution 6 everywhere.** Rejected: the blur reports came back —
  resolution 7 was the floor the pilot data supported.
- **Resolution 7 default, 9 as a named exception.** The urban pilots
  keep 9 with an explicit config entry; everything else standardizes.

## Decision

Resolution 7 is the geo-alert default (`repos/beacon/config/geo.yaml`);
resolution 9 requires a named exception in the config. The decision is
recorded 2026-06-08 and the migration completed 2026-06-12.

## Consequences

- Evaluator p95 dropped 9.4s → 0.8s on the standard regions.
- Border blur reports stopped with the last polygon region.
- Adding a resolution-9 region now requires editing the exception list,
  which makes the cost visible at review time.

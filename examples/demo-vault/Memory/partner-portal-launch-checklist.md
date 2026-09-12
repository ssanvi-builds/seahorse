---
id: 019f4651-d978-7cea-ac79-774eb353588c
created_at: '2026-07-09T09:59:57.048906Z'
schema_version: 1.0.0
provenance:
  agent_id: consolidator
  confidence: 0.91
  extraction_mode: consolidated
  model_used: claude-sonnet-5
  prompt_hash: 1fa2aa6c97858cf396aa7b16adef4639fa6e4157bf1129c0c97722e4ae9e46f6
  session_id: consolidate-019f4651-d978-7245-b153-7dd05aa1c490
  source_type: system
supersedes: 019f224a-4897-734c-a116-16a313e76a7c
supersedes_reason: merge
cognitive_type: semantic
source_type: system
title: partner portal launch checklist
summary: "The [[Herald]] launch checklist closed with exporter-only assets,\nread-only routes verified, and an automated row-level security\nspot-check per release."
tags: []
---
# partner portal launch checklist

The [[Herald]] launch checklist closed with exporter-only assets,
read-only routes verified, and an automated row-level security
spot-check per release.

## Context

The first partner cohort was scheduled for July, and the portal sits on
top of three systems at once: the [[Drift]] static exporter, [[Ember]]
service tokens, and the versioned data API. The checklist ran across
three sessions: the scoping, the open items, and the closure.

## Options considered

- **Ship on the Superset embed path.** Rejected: the license gray zone
  that already pushed the exporter decision.
- **Manual RLS review per release.** Rejected: reviews that depend on
  memory do not survive a busy release week; the check belongs in the
  release job.
- **Exporter-only assets + automated RLS spot-check.** No live queries,
  no embed path, and the security property is enforced where the assets
  are built.

## Decision

Launch checklist: exporter-only assets, read-only routes (audited: 0
non-GET), [[Ember]] service tokens, automated RLS spot-check in the
release job. Config lives in `repos/herald/config/launch.yaml`.

## Consequences

- Portal launched 2026-07-09 with zero CSP surprises (status note).
- [[Lena Fischer]]'s row-level security is enforced at export time and
  spot-checked on every release — two layers, both automatic.
- The sunset-date tracker ships with the portal so partner dashboards
  see API deprecations before they land.

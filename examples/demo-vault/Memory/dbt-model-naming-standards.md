---
id: 019e3ad1-c3c6-736f-b20a-c99696e4adcd
created_at: '2026-05-18T11:21:26.214663Z'
schema_version: 1.0.0
provenance:
  agent_id: consolidator
  confidence: 0.92
  extraction_mode: consolidated
  model_used: claude-sonnet-5
  prompt_hash: 15a4a1effa08d80533445ccb8f14e2cc7c3ce7f1b15165c323b710e8d8301389
  session_id: consolidate-019e3ad1-c3c6-7b3b-b47f-a2e6d56fb0c6
  source_type: system
supersedes: 019df271-a6f8-7a51-862f-2449b3f61fd9
supersedes_reason: merge
cognitive_type: semantic
source_type: system
title: dbt model naming standards
summary: "dbt models follow the staging/mart split with a linter (PR 195) that\nfails the build on name collisions or prefix violations."
tags: []
---
# dbt model naming standards

dbt models follow the staging/mart split with a linter (PR 195) that
fails the build on name collisions or prefix violations.

## Context

Eleven model names collided across the [[Drift]] and [[Cinder]] repos —
the same name meant a staging view in one and a published mart in the
other, and the confusion reached a partner-facing dashboard. The work
ran across three sessions: the collision audit, the split proposal, and
the linter merge.

## Options considered

- **Renumber the collisions as they surface.** Rejected: whack-a-mole;
  the audit found eleven and nobody believes that is all of them.
- **Move everything into one dbt project.** Rejected: the deploy
  cadences differ and the merge would be a quarter of churn for
  hygiene.
- **Staging/mart split + a build-failing linter per repo.** Enforced at
  the point where a bad name can actually merge.

## Decision

Staging models clean (`stg_` prefix), marts publish (`mart_` prefix),
and the linter fails the build on collisions or violations in both
repos. [[Lena Fischer]] merged PR 195.

## Consequences

- Collisions: zero since the rename; the semantic note records the
  standing rule.
- The partner-facing confusion class is closed: a mart name resolves
  to exactly one published model.
- New repos adopt the linter on day one — the checklist in the portal
  launch references it.

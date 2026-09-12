---
id: 01a047c9-64e9-7688-805d-45d19a66447e
created_at: '2026-08-28T09:52:56.041778Z'
schema_version: 1.0.0
provenance:
  agent_id: consolidator
  confidence: 0.87
  extraction_mode: consolidated
  model_used: claude-sonnet-5
  prompt_hash: b776c7fbe55371f78f0e856b4ef31e6bd8f125bb9451c1110908878b6d9909d3
  session_id: consolidate-01a047c9-64e9-7dfc-8e3f-8a95dfe7ccb2
  source_type: system
supersedes: 01a0239e-e8f6-7d44-b18a-3f5dd721dd68
supersedes_reason: merge
cognitive_type: semantic
source_type: system
title: openapi client sdk rollout
summary: "Both client teams moved to SDKs generated from the CI-published OpenAPI\nspec; hand-written clients are retired."
tags: []
---
# openapi client sdk rollout

Both client teams moved to SDKs generated from the CI-published OpenAPI
spec; hand-written clients are retired.

## Context

The URL-path versioning decision only holds if clients actually track
versions, and hand-written clients drift — three drift incidents in 90
days. The rollout ran across three sessions: the drift audit, the
generator plan, and the cutover.

## Options considered

- **Publish a typed client library, maintained by us.** Rejected: a
  second source of truth; the spec in CI already is the contract.
- **Let each team keep hand-written clients.** Rejected: the drift
  incidents are the counterargument.
- **Generate SDKs from the CI spec.** The generator reads what CI
  published, nothing else, and the sunset-date tracker rides along.

## Decision

SDKs are generated from the CI-published spec (`make sdk VERSION=<v>`);
manual edits to generated code fail review. The semantic note records
the standing rule (2026-08-10); the rollout completed 2026-08-28.

## Consequences

- Hand-written clients: zero (verified in the cutover session).
- The sunset-date tracker gives client dashboards the deprecation
  clock the versioning decision promised.
- [[Noah Bennett]]'s mobile team validated the generator on the
  strictest client first, which flushed out the edge cases early.

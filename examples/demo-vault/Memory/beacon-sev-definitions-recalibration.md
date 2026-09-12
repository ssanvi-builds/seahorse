---
id: 019fa30a-1218-7f28-bb39-63b655b883dc
created_at: '2026-07-27T10:06:14.040399Z'
schema_version: 1.0.0
provenance:
  agent_id: consolidator
  confidence: 0.92
  extraction_mode: consolidated
  model_used: claude-sonnet-5
  prompt_hash: a0ef1b419442fbd12c06f016252acd861686306e6d1b62b833b8be64aa022b6f
  session_id: consolidate-019fa30a-1218-7ffc-bca2-fa5800ab9ec9
  source_type: system
supersedes: 019f5af2-3fb5-7761-96b6-5c5ac303bb84
supersedes_reason: merge
cognitive_type: semantic
source_type: system
title: beacon sev definitions recalibration
summary: "Beacon severities are response-time scoped (5m / 15m / working day) and\nthe routing table maps from them — sev-2 pages, always, for everyone."
tags: []
---
# beacon sev definitions recalibration

Beacon severities are response-time scoped (5m / 15m / working day) and
the routing table maps from them — sev-2 pages, always, for everyone.

## Context

The sev definitions predated the routing split and were team-scoped:
sev-2 paged for some teams and merely informed for others, which made
the page-vs-inform boundary folklore instead of config. The
recalibration ran across three sessions: the double-duty report, the
response-time proposal, and the ship.

## Options considered

- **Per-team sev overrides.** Rejected: more config surface, same
  folklore, and cross-team alerts still ambiguous.
- **Add a sev-4 for informational.** Rejected: renames the problem —
  the ambiguity was in sev-2, not a missing level.
- **Response-time scoped definitions.** A sev is what the responder
  owes: 5 minutes, 15 minutes, the working day. Routing maps from the
  definition, so the split is mechanical.

## Decision

Severity definitions live in `repos/beacon/config/sev.yaml`, scoped by
response time; the semantic note records the meanings. [[Iris Kovac]]
signed off on the rota implications.

## Consequences

- sev-2 pages uniformly (41/41 paged in the first week, zero
  informed-only).
- The ack-sync expectation per sev is now stated in config, not in
  onboarding folklore.
- The geo-alert and lag panels map their alerts to the shared
  definitions, which ended the one-off severity debates.

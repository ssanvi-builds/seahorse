---
id: 019d2963-c36f-76a3-b968-139734f2e22d
created_at: '2026-03-26T09:04:57.199693Z'
schema_version: 1.0.0
provenance:
  agent_id: consolidator
  confidence: 0.89
  extraction_mode: consolidated
  model_used: claude-sonnet-5
  prompt_hash: 06965a8b6b1d969d29ecb0549e02a79ee2beeb5b3d14d3e241774d762edbdfa4
  session_id: consolidate-019d2963-c36f-7a43-bc55-d08ce169d25b
  source_type: system
supersedes: 019ce17d-67b7-79eb-96f0-936e6795c7b3
supersedes_reason: merge
cognitive_type: semantic
source_type: system
title: ember token lifecycle review
summary: Ember issues 1-hour access tokens with rotation and refresh tokens; the 15-minute TTL is retired.
tags: []
---
# ember token lifecycle review

Ember issues 1-hour access tokens with rotation and refresh tokens; the 15-minute TTL is retired.

## Context

The original 15-minute access token TTL forced the mobile clients into a
refresh every ~12 minutes (p50 from the audit data), and battery-drain
reports reached support faster than the tokens expired. [[Noah Bennett]]'s
mobile team escalated, and the review ran across three sessions: the
problem report, the proposal, and the security clearance.

## Options considered

- **Keep 15 minutes, tune clients.** Rejected: the refresh traffic is
  inherent to the TTL, not a client bug, and the escalation had executive
  attention.
- **1-hour TTL, no rotation.** Rejected by the security review: a leaked
  token would stay valid an hour with no revocation signal.
- **1-hour TTL + rotation + refresh tokens.** Rotation emits an audit entry
  per rotation, which gives the revocation signal the review wanted;
  [[Sam Whitaker]] documented the rotation audit format the same week.

## Decision

Access tokens live 1 hour, with rotation enabled and refresh tokens issued
to clients that need silent renewal. Every rotation writes an audit entry
the Ember audit log keeps (180-day retention since 2026-05-26).

## Consequences

- Refresh traffic drops from every ~12 minutes to the hourly boundary —
  the battery reports stop.
- A leaked access token is valid up to an hour, but the rotation audit
  entries give the security team an anomaly signal they did not have before.
- The TTL migration shipped 2026-04-29; the config lives in
  `repos/ember/config/tokens.yaml`.

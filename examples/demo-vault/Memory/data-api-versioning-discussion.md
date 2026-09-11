---
id: 019f891e-de62-7426-acc4-b3f868fd2f30
created_at: '2026-07-22T09:18:49.442168Z'
schema_version: 1.0.0
provenance:
  agent_id: consolidator
  confidence: 0.92
  extraction_mode: consolidated
  model_used: claude-sonnet-5
  prompt_hash: 67330afcda054288d5cdb702fc980692fbfa82c6d5e352e69f6bcc76d2bf7fcb
  session_id: consolidate-019f891e-de62-7f67-80e7-49f2a78dcff8
  source_type: system
supersedes: 019f411f-d155-723b-8b43-f87d8b5856b4
supersedes_reason: merge
cognitive_type: semantic
source_type: system
title: data api versioning discussion
summary: The data API versions through URL paths (`/v1/...`) with a 6-month deprecation window and sunset headers.
tags: []
---
# data api versioning discussion

The data API versions through URL paths (`/v1/...`) with a 6-month deprecation window and sunset headers.

## Context

Two client teams broke on the same schema change in one week, and the API
was about to leave beta without any versioning rule. The discussion ran
across three sessions: the break report, the candidate schemes, and the
gateway draft.

## Options considered

- **Version in request headers.** Rejected: invisible in logs, uncacheable
  at the gateway, and clients forgot to send it — the breaks would continue.
- **Media-type versioning.** Rejected: correct but expensive to adopt —
  every client's HTTP layer needs changes before the first versioned call.
- **URL paths.** Visible, cacheable, diffable in access logs, and the
  gateway can route and deprecate versions with plain rules.

## Decision

Versions live in the URL path (`/v1/...`). A deprecated version keeps
answering for 6 months with a `Sunset` header carrying the removal date,
then the gateway drops the route. The OpenAPI spec is generated in CI
(decision recorded 2026-06-11) so the docs cannot drift from the routes.

## Consequences

- Breaking changes ship as a new path, never as a surprise inside `/v1`.
- The 6-month window is a commitment the platform makes to client teams;
  the sunset date is machine-readable, so client dashboards can track it.
- Version count discipline: a new path needs a deprecation plan for the one
  it replaces, or the gateway rules reject the deploy.

---
id: 019f17f1-db91-7a13-a4b9-736ba2fca98c
created_at: '2026-06-30T09:52:34.193618Z'
schema_version: 1.0.0
provenance:
  agent_id: consolidator
  confidence: 0.89
  extraction_mode: consolidated
  model_used: claude-sonnet-5
  prompt_hash: 5129c6440b27b22b73e19546ce10568d1aee95df26ab99e7e939a526afb3e997
  session_id: consolidate-019f17f1-db91-79ac-9dac-3434d4ce9346
  source_type: system
supersedes: 019ecfb5-6180-701f-a945-1584309e9b1d
supersedes_reason: merge
cognitive_type: semantic
source_type: system
title: drift dashboard embedding options
summary: Drift embeds go through a static exporter that rebuilds dashboards every 15 minutes, replacing the Superset-embedded setup.
tags: []
---
# drift dashboard embedding options

Drift embeds go through a static exporter that rebuilds dashboards every 15 minutes, replacing the Superset-embedded setup.

## Context

The Superset license terms for embedded dashboards changed: internal embeds
stayed fine, but the partner portal landed in a gray zone nobody wanted to
explain to legal. The review ran across three sessions: the license problem,
the candidate approaches, and the cutover.

## Options considered

- **iframe per tool.** Rejected: each tool ships its own auth story and the
  row-level security policy would be enforced in three different places.
- **Superset embedded SDK.** Rejected: it is exactly the license exposure
  that started the review.
- **Static exporter.** Rebuilds the dashboards as static assets on a
  schedule; row-level security is enforced once, at export time.

## Decision

The exporter rebuilds dashboards as static assets every 15 minutes (the
refresh-cadence chain records the move from hourly). Row-level security is
applied at export time — `repos/drift/config/embed.yaml` is the config.

## Consequences

- No license exposure on the partner portal.
- Interactivity is limited to what a static asset can do — filter requests
  that need live queries route to the data API instead.
- The 15-minute rebuild replaced the hourly one (2026-06-30) because the
  exporter made it cheaper than the old full rebuild.
- Row-level security is enforced in exactly one place, which is what
  [[Lena Fischer]]'s policy review wanted all along.

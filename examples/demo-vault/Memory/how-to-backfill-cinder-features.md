---
id: 019e25f4-f7c9-789a-9262-76bbd2216868
created_at: '2026-05-14T10:07:51.753313Z'
schema_version: 1.0.0
provenance:
  agent_id: seahorse/claude-code
  confidence: 0.93
  extraction_mode: llm
  model_used: claude-sonnet-5
  prompt_hash: 30f76b8fe9bf225f3221ee31454c287240c4f3e73b9bb848fe6bb77360294647
  session_id: 6e95905a-e753-4ad1-9139-146aefcb0a29
  source_type: agent
cognitive_type: procedural
source_type: agent
title: How to backfill Cinder features
summary: Point-in-time correctness is the contract; the backfill job enforces it.
tags: []
---
Point-in-time correctness is the contract; the backfill job enforces it.

1. Identify the feature view and the entity window in [[Cinder]]
2. `cinder-backfill --view <view> --from <date> --dry-run`
3. Check the point-in-time join report against the training cutoffs
4. Run for real, then verify row counts in the feature registry
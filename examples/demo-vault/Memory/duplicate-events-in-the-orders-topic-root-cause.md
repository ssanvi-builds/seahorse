---
id: 019cb324-2189-7c4d-ae04-7fe385ef404f
created_at: '2026-03-03T10:00:15.497282Z'
schema_version: 1.0.0
provenance:
  agent_id: seahorse/claude-code
  confidence: 0.87
  extraction_mode: llm
  model_used: claude-sonnet-5
  prompt_hash: 582aeb381526df3743b3d8a2e9eb88dc472e010f915bcdd81d92ab51f6461aa1
  session_id: 412b8b92-cbe6-4dbf-8e64-7731d473a384
  source_type: agent
valid_at: '2026-03-03T00:00:00Z'
cognitive_type: episodic
source_type: agent
title: Duplicate events in the orders topic root cause
summary: 'Not a Kafka bug: a hand-run backfill script replayed a window of the orders topic twice while exactly-once was disabled on that path.'
tags: []
---
Not a Kafka bug: a hand-run backfill script replayed a window of the orders topic twice while exactly-once was disabled on that path. Follow-up: the replay tooling in progress must refuse double replays by default.
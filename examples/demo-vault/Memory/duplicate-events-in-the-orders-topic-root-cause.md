---
id: 019cb323-a052-755a-b084-0771274a2e06
created_at: '2026-03-03T09:59:42.418627Z'
schema_version: 1.0.0
provenance:
  agent_id: seahorse/claude-code
  confidence: 0.85
  extraction_mode: llm
  model_used: claude-sonnet-5
  prompt_hash: 582aeb381526df3743b3d8a2e9eb88dc472e010f915bcdd81d92ab51f6461aa1
  session_id: 796b0ade-16d2-4999-b9b0-a28c8ee20f29
  source_type: agent
valid_at: '2026-03-03T00:00:00Z'
cognitive_type: episodic
source_type: agent
title: Duplicate events in the orders topic root cause
summary: 'Not a Kafka bug: a hand-run backfill script replayed a window of the orders topic twice while exactly-once was disabled on that path.'
tags: []
---
Not a Kafka bug: a hand-run backfill script replayed a window of the orders topic twice while exactly-once was disabled on that path. Follow-up: the replay tooling in progress must refuse double replays by default.
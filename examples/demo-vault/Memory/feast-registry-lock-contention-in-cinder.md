---
id: 019db47e-1d35-7272-a5e1-47a5b150a9e6
created_at: '2026-04-22T09:20:57.141303Z'
schema_version: 1.0.0
provenance:
  agent_id: seahorse/claude-code
  confidence: 0.86
  extraction_mode: llm
  model_used: claude-sonnet-5
  prompt_hash: 8602d67c7637e5a93e13a3246542d8f69d359690eb06b4def5905d4c2f31af90
  session_id: f20ac1ee-182e-47b9-bbef-cd4ede0f3d0f
  source_type: agent
valid_at: '2026-04-22T00:00:00Z'
cognitive_type: episodic
source_type: agent
title: Feast registry lock contention in Cinder
summary: Concurrent backfills contended on the Feast registry lock and stalled a nightly training.
tags: []
---
Concurrent backfills contended on the Feast registry lock and stalled a nightly training. Registry writes are serialized now; the contention is gone.
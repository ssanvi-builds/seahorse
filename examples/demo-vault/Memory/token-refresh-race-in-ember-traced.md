---
id: 019d6c86-e68f-7dd1-a1ed-55710a2c1a0e
created_at: '2026-04-08T09:57:53.423887Z'
schema_version: 1.0.0
provenance:
  agent_id: seahorse/claude-code
  confidence: 0.9
  extraction_mode: llm
  model_used: claude-sonnet-5
  prompt_hash: 3773f265db54a8bb240342f029f88ad0f51f8dae9d215226bf7915cf988adf4e
  session_id: 73f1671d-0044-48a5-b2d1-f285fe0a3012
  source_type: agent
valid_at: '2026-04-08T00:00:00Z'
cognitive_type: episodic
source_type: agent
title: Token refresh race in Ember traced
summary: Two refresh requests raced on the same token family; the loser invalidated the winner.
tags: []
---
Two refresh requests raced on the same token family; the loser invalidated the winner. Fixed with per-family refresh serialization in Ember.
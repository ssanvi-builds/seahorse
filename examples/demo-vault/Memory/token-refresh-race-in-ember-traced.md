---
id: 019d6cb0-bc85-7d51-bd5b-12d069b6a67a
created_at: '2026-04-08T10:43:35.173834Z'
schema_version: 1.0.0
provenance:
  agent_id: seahorse/claude-code
  confidence: 0.93
  extraction_mode: llm
  model_used: claude-sonnet-5
  prompt_hash: 3773f265db54a8bb240342f029f88ad0f51f8dae9d215226bf7915cf988adf4e
  session_id: e64f5107-d473-4773-ac34-294ce80b2496
  source_type: agent
valid_at: '2026-04-08T00:00:00Z'
cognitive_type: episodic
source_type: agent
title: Token refresh race in Ember traced
summary: Two refresh requests raced on the same token family; the loser invalidated the winner.
tags: []
---
Two refresh requests raced on the same token family; the loser invalidated the winner. Fixed with per-family refresh serialization in [[Ember]].
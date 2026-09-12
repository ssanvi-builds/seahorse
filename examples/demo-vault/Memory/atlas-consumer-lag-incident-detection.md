---
id: 019f6016-e21e-7d76-a15f-c23fa3ab518f
created_at: '2026-07-14T10:05:40.254980Z'
schema_version: 1.0.0
provenance:
  agent_id: seahorse/claude-code
  confidence: 0.87
  extraction_mode: llm
  model_used: claude-sonnet-5
  prompt_hash: 15eec5d96ab228750a3220e7931cd1b7ee7903c36c2af64af0c409ccaccd66d6
  session_id: 4a205b6f-e6cb-442f-aa3e-a3c6561056f3
  source_type: agent
valid_at: '2026-07-14T00:00:00Z'
cognitive_type: episodic
source_type: agent
title: 'Atlas consumer lag incident: detection'
summary: '[[Beacon]] sev-2 fired at 06:12 UTC: p99 lag on the orders topic past 40s.'
tags: []
---
[[Beacon]] sev-2 fired at 06:12 UTC: p99 lag on the orders topic past 40s. First suspect was a slow partition; the consumer group events told another story.
---
id: 019f603f-60c6-7dce-b9ab-26558b427067
created_at: '2026-07-14T10:49:54.118048Z'
schema_version: 1.0.0
provenance:
  agent_id: seahorse/claude-code
  confidence: 0.96
  extraction_mode: llm
  model_used: claude-sonnet-5
  prompt_hash: b8a786ace267d16dcee3c8a9e576c7916362c02a717145d88cf3a15a84dd5c37
  session_id: 4a205b6f-e6cb-442f-aa3e-a3c6561056f3
  source_type: agent
valid_at: '2026-07-14T00:00:00Z'
cognitive_type: episodic
source_type: agent
title: 'Atlas consumer lag incident: triage'
summary: 47 rebalances in one hour, all correlated with consumer restarts from the morning deploy.
tags: []
---
47 rebalances in one hour, all correlated with consumer restarts from the morning deploy. The storm, not the partitions, was starving throughput.
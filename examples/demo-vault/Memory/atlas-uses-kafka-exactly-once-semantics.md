---
id: 019c8eee-0ed5-723d-b814-46226981418f
created_at: '2026-02-24T09:14:51.989935Z'
schema_version: 1.0.0
provenance:
  agent_id: seahorse/claude-code
  confidence: 0.86
  extraction_mode: llm
  model_used: claude-sonnet-5
  prompt_hash: d68dd4915be34639fe952aa5e084ac7ee8bff2baf2a77e88dc2dbd49efe649f8
  session_id: 3d750743-54ed-4bc8-bf4a-b0b73b8d375c
  source_type: agent
valid_at: '2026-02-24T00:00:00Z'
cognitive_type: semantic
source_type: agent
title: Atlas uses Kafka exactly-once semantics
summary: 'We accept the throughput cost: duplicate orders events are worse than a slower ingest.'
tags: []
---
We accept the throughput cost: duplicate orders events are worse than a slower ingest. The orders topic relies on it for dedup.
---
id: 019c8ee8-bcc9-70a3-b072-00f953a7c7da
created_at: '2026-02-24T09:09:03.305471Z'
schema_version: 1.0.0
provenance:
  agent_id: seahorse/claude-code
  confidence: 0.87
  extraction_mode: llm
  model_used: claude-sonnet-5
  prompt_hash: d68dd4915be34639fe952aa5e084ac7ee8bff2baf2a77e88dc2dbd49efe649f8
  session_id: 66399965-12a0-41b4-b90e-1e1aa489e7f2
  source_type: agent
valid_at: '2026-02-24T00:00:00Z'
cognitive_type: semantic
source_type: agent
title: Atlas uses Kafka exactly-once semantics
summary: 'We accept the throughput cost: duplicate orders events are worse than a slower ingest.'
tags: []
---
We accept the throughput cost: duplicate orders events are worse than a slower ingest. The orders topic relies on it for dedup.
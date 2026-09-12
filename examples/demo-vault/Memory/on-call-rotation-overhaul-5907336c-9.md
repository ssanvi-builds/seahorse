---
id: 019cf615-e35c-7d51-a8b4-75118cf1f5d3
created_at: '2026-03-16T09:59:15.548507Z'
schema_version: 1.0.0
provenance:
  agent_id: unknown
  confidence: 1.0
  extraction_mode: skip
  model_used: null
  prompt_hash: null
  session_id: 5907336c-dd29-46b1-a312-1f6945ee7bd1
  source_type: agent
cognitive_type: episodic
source_type: agent
title: on-call rotation overhaul [5907336c:9]
summary: "## User prompt\n\non-call rotation overhaul\nhandover gaps keep dropping open sevs, so the rotation mechanics need a review before the next quarter starts."
tags: []
---
# on-call rotation overhaul [5907336c:9]

## User prompt

on-call rotation overhaul
handover gaps keep dropping open sevs, so the rotation mechanics need a review before the next quarter starts.

### Bash
tool_use_id: toolu_01L9V07hWK5fpd5mBAmP14Xy
input: {"command": "rota-stats handovers --last 90d"}
response: handovers: 12, dropped_sevs: 4, ack_sync: false
---
id: 019dce3c-1f46-79c8-aa0d-c73b4ae5c059
created_at: '2026-04-27T09:18:59.910741Z'
schema_version: 1.0.0
provenance:
  agent_id: unknown
  confidence: 1.0
  extraction_mode: skip
  model_used: null
  prompt_hash: null
  session_id: e69a75c0-fce3-45e3-9aaa-674be656be8c
  source_type: agent
cognitive_type: episodic
source_type: agent
title: on-call rotation overhaul [e69a75c0:7]
summary: "## User prompt\n\non-call rotation overhaul\nrotation v2 is live: one calendar owned by Iris, acks sync from Beacon, and the first week had zero dropped sevs."
tags: []
---
# on-call rotation overhaul [e69a75c0:7]

## User prompt

on-call rotation overhaul
rotation v2 is live: one calendar owned by Iris, acks sync from Beacon, and the first week had zero dropped sevs.

### Bash
tool_use_id: toolu_01mDE6t8KYJZUVWFislmV8F0
input: {"command": "rota-stats handovers --last 7d"}
response: handovers: 1, dropped_sevs: 0, ack_sync: true
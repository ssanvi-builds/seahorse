---
id: 019f6a64-5c44-7314-a0b4-a23870cd674e
created_at: '2026-07-16T10:06:29.956740Z'
schema_version: 1.0.0
provenance:
  agent_id: unknown
  confidence: 1.0
  extraction_mode: skip
  model_used: null
  prompt_hash: null
  session_id: 349445bf-61b9-4e74-b775-452ba169e049
  source_type: agent
cognitive_type: episodic
source_type: agent
title: 'incident review: atlas consumer lag [349445bf:9]'
summary: "## User prompt\n\nincident review: atlas consumer lag\nretro time: the timeline, the action items, and the deploy guard that should have caught this in the first place."
tags: []
---
# incident review: atlas consumer lag [349445bf:9]

## User prompt

incident review: atlas consumer lag
retro time: the timeline, the action items, and the deploy guard that should have caught this in the first place.

### Bash
tool_use_id: toolu_01AKf4LhCbJ3OH458voi2YGA
input: {"command": "atlas-admin consumers --describe"}
response: group: orders-ingest, rebalances_1h: 0
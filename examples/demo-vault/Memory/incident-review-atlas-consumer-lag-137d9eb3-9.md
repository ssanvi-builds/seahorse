---
id: 019f6a5b-02c2-7e85-beea-e5403161da0b
created_at: '2026-07-16T09:56:17.218369Z'
schema_version: 1.0.0
provenance:
  agent_id: unknown
  confidence: 1.0
  extraction_mode: skip
  model_used: null
  prompt_hash: null
  session_id: 137d9eb3-5907-4e00-8289-d881436cf3f6
  source_type: agent
cognitive_type: episodic
source_type: agent
title: 'incident review: atlas consumer lag [137d9eb3:9]'
summary: "## User prompt\n\nincident review: atlas consumer lag\nretro time: the timeline, the action items, and the deploy guard that should have caught this in the first place."
tags: []
---
# incident review: atlas consumer lag [137d9eb3:9]

## User prompt

incident review: atlas consumer lag
retro time: the timeline, the action items, and the deploy guard that should have caught this in the first place.

### Bash
tool_use_id: toolu_01BaalojlgrKxhpArGC0EmuA
input: {"command": "atlas-admin consumers --describe"}
response: group: orders-ingest, rebalances_1h: 0
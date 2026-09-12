---
id: 019f5fdd-7453-78bf-8eab-14cd9c3c4399
created_at: '2026-07-14T09:02:56.595858Z'
schema_version: 1.0.0
provenance:
  agent_id: unknown
  confidence: 1.0
  extraction_mode: skip
  model_used: null
  prompt_hash: null
  session_id: 7139c3f7-03bd-47f7-8c53-46c68ddc4ebc
  source_type: agent
cognitive_type: episodic
source_type: agent
title: 'incident review: atlas consumer lag [7139c3f7:2]'
summary: "## User prompt\n\nincident review: atlas consumer lag\nlag alert firing on orders with p99 climbing past 40s, so I am looking at the consumer group events first."
tags: []
---
# incident review: atlas consumer lag [7139c3f7:2]

## User prompt

incident review: atlas consumer lag
lag alert firing on orders with p99 climbing past 40s, so I am looking at the consumer group events first.

### Bash
tool_use_id: toolu_01YifBwGwSWlc2NZAx8Zrpy0
input: {"command": "atlas-admin consumers --describe"}
response: group: orders-ingest, members: 12, rebalances_1h: 47
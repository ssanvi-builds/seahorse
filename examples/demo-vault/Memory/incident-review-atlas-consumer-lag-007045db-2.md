---
id: 019f5fdb-2e7f-7689-8e57-c36e420ecd18
created_at: '2026-07-14T09:00:27.647486Z'
schema_version: 1.0.0
provenance:
  agent_id: unknown
  confidence: 1.0
  extraction_mode: skip
  model_used: null
  prompt_hash: null
  session_id: 007045db-a1e6-4e3b-b991-8f0b8fdb9a30
  source_type: agent
cognitive_type: episodic
source_type: agent
title: 'incident review: atlas consumer lag [007045db:2]'
summary: "## User prompt\n\nincident review: atlas consumer lag\nlag alert firing on orders with p99 climbing past 40s, so I am looking at the consumer group events first."
tags: []
---
# incident review: atlas consumer lag [007045db:2]

## User prompt

incident review: atlas consumer lag
lag alert firing on orders with p99 climbing past 40s, so I am looking at the consumer group events first.

### Bash
tool_use_id: toolu_01FXzWnRRmWF2WtBr4NHLXZP
input: {"command": "atlas-admin consumers --describe"}
response: group: orders-ingest, members: 12, rebalances_1h: 47
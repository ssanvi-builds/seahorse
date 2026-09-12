---
id: 019e82bf-e7b5-7271-9b7d-227b1f485f9d
created_at: '2026-06-01T10:34:35.317523Z'
schema_version: 1.0.0
provenance:
  agent_id: unknown
  confidence: 1.0
  extraction_mode: skip
  model_used: null
  prompt_hash: null
  session_id: a245084c-20b4-41b7-85d4-68c19ccb128a
  source_type: agent
cognitive_type: episodic
source_type: agent
title: beacon sev definitions recalibration [a245084c:5]
summary: "## User prompt\n\nbeacon sev definitions recalibration\nthe sev definitions predate the routing split, and sev-2 is doing double duty: it pages for some teams and informs for others."
tags: []
---
# beacon sev definitions recalibration [a245084c:5]

## User prompt

beacon sev definitions recalibration
the sev definitions predate the routing split, and sev-2 is doing double duty: it pages for some teams and informs for others.

### Bash
tool_use_id: toolu_01n7Qd2fjcy3PM60pajhp960
input: {"command": "beacon-stats sevs --last 30d"}
response: sev2_total: 214, paged: 96, informed: 118
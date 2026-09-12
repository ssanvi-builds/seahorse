---
id: 019f36d4-c3a3-7866-ae77-0754010fc1d0
created_at: '2026-07-06T09:49:01.219888Z'
schema_version: 1.0.0
provenance:
  agent_id: unknown
  confidence: 1.0
  extraction_mode: skip
  model_used: null
  prompt_hash: null
  session_id: 6a1f7cd7-ed6c-452b-b05d-2e3b812915c5
  source_type: agent
cognitive_type: episodic
source_type: agent
title: kafka broker upgrade plan [6a1f7cd7:8]
summary: "## User prompt\n\nkafka broker upgrade plan\nupgrade complete: all brokers on 3.8, consumer groups untouched, p99 lag flat through both windows."
tags: []
---
# kafka broker upgrade plan [6a1f7cd7:8]

## User prompt

kafka broker upgrade plan
upgrade complete: all brokers on 3.8, consumer groups untouched, p99 lag flat through both windows.

### Bash
tool_use_id: toolu_01uCkr5Y77rw5fw0Hx1Yqxcy
input: {"command": "atlas-admin brokers --version"}
response: brokers: 12, version: 3.8.1, rebalances: 0
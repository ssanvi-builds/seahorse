---
id: 019f5af2-3fb5-7761-96b6-5c5ac303bb84
created_at: '2026-07-13T10:07:33.301107Z'
schema_version: 1.0.0
provenance:
  agent_id: unknown
  confidence: 1.0
  extraction_mode: skip
  model_used: null
  prompt_hash: null
  session_id: cd553ad2-e3df-4156-b584-f1c127b30901
  source_type: agent
cognitive_type: episodic
source_type: agent
title: beacon sev definitions recalibration [cd553ad2:6]
summary: "## User prompt\n\nbeacon sev definitions recalibration\nshipped: definitions are response-time scoped, the routing table maps from them, and the page-vs-inform split is gone from team folklore."
tags: []
---
# beacon sev definitions recalibration [cd553ad2:6]

## User prompt

beacon sev definitions recalibration
shipped: definitions are response-time scoped, the routing table maps from them, and the page-vs-inform split is gone from team folklore.

### Bash
tool_use_id: toolu_012JTTxhfFwrNLonYUzmdYtJ
input: {"command": "beacon-stats sevs --last 7d"}
response: sev2_total: 41, paged: 41, informed: 0
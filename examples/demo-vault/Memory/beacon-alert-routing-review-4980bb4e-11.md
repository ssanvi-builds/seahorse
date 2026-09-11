---
id: 019d675b-4f62-7543-afa6-9b585c73c80e
created_at: '2026-04-07T09:52:10.594969Z'
schema_version: 1.0.0
provenance:
  agent_id: unknown
  confidence: 1.0
  extraction_mode: skip
  model_used: null
  prompt_hash: null
  session_id: 4980bb4e-0291-4968-80ba-dbcd59c0630e
  source_type: agent
cognitive_type: episodic
source_type: agent
title: beacon alert routing review [4980bb4e:11]
summary: "## User prompt\n\nbeacon alert routing review\ntuning done: sev definitions live in the config, ack sync is live, and noise is down 40% in the first week."
tags: []
---
# beacon alert routing review [4980bb4e:11]

## User prompt

beacon alert routing review
tuning done: sev definitions live in the config, ack sync is live, and noise is down 40% in the first week.

### Bash
tool_use_id: toolu_01UDnPhxFYmUaYTsesJk7ZwZ
input: {"command": "beacon-stats alerts --last 7d"}
response: total: 487, acked_within_5m: 83%
---
id: 019d6762-4787-78ff-96b6-b940105ffd58
created_at: '2026-04-07T09:59:47.335210Z'
schema_version: 1.0.0
provenance:
  agent_id: unknown
  confidence: 1.0
  extraction_mode: skip
  model_used: null
  prompt_hash: null
  session_id: ce9c8cd2-371f-4136-b3ad-3c7a7db4c111
  source_type: agent
cognitive_type: episodic
source_type: agent
title: beacon alert routing review [ce9c8cd2:11]
summary: "## User prompt\n\nbeacon alert routing review\ntuning done: sev definitions live in the config, ack sync is live, and noise is down 40% in the first week."
tags: []
---
# beacon alert routing review [ce9c8cd2:11]

## User prompt

beacon alert routing review
tuning done: sev definitions live in the config, ack sync is live, and noise is down 40% in the first week.

### Bash
tool_use_id: toolu_01Zy5tuJOe80vceUKvqNZ6HX
input: {"command": "beacon-stats alerts --last 7d"}
response: total: 487, acked_within_5m: 83%
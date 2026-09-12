---
id: 019ea6b1-26df-71ed-a61b-51e2c0dae8b3
created_at: '2026-06-08T10:04:48.223853Z'
schema_version: 1.0.0
provenance:
  agent_id: unknown
  confidence: 1.0
  extraction_mode: skip
  model_used: null
  prompt_hash: null
  session_id: e7c3a576-43f4-4062-b6b4-bb6f7d599fdf
  source_type: agent
cognitive_type: episodic
source_type: agent
title: kafka broker upgrade plan [e7c3a576:6]
summary: "## User prompt\n\nkafka broker upgrade plan\nthe brokers are two minor versions behind and the support window for the current release closes this quarter."
tags: []
---
# kafka broker upgrade plan [e7c3a576:6]

## User prompt

kafka broker upgrade plan
the brokers are two minor versions behind and the support window for the current release closes this quarter.

### Bash
tool_use_id: toolu_01ynzvrsf48XeFJC9CISnu0l
input: {"command": "atlas-admin brokers --version"}
response: brokers: 12, version: 3.6.2, eol: 2026-09
---
id: 019d3e0b-0e32-7042-92eb-4a0d58c009d9
created_at: '2026-03-30T09:20:05.170484Z'
schema_version: 1.0.0
provenance:
  agent_id: unknown
  confidence: 1.0
  extraction_mode: skip
  model_used: null
  prompt_hash: null
  session_id: 0ca27095-4fc7-4788-b7a0-6e5465de9ee2
  source_type: agent
cognitive_type: episodic
source_type: agent
title: dbt model naming standards [0ca27095:6]
summary: "## User prompt\n\ndbt model naming standards\nthe same model name means different things across the drift and cinder repos, and the confusion reached a partner-facing dashboard last week."
tags: []
---
# dbt model naming standards [0ca27095:6]

## User prompt

dbt model naming standards
the same model name means different things across the drift and cinder repos, and the confusion reached a partner-facing dashboard last week.

### Bash
tool_use_id: toolu_01XPSK7iKvf7709qh6Q4UbHV
input: {"command": "dbt-ls --duplicates"}
response: name_collisions: 11, repos: [drift, cinder]
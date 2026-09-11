---
id: 019e63b4-8917-717d-85f7-fbd313803c72
created_at: '2026-05-26T09:53:56.503831Z'
schema_version: 1.0.0
provenance:
  agent_id: unknown
  confidence: 1.0
  extraction_mode: skip
  model_used: null
  prompt_hash: null
  session_id: 5120bc32-7f0e-4f6b-a0f7-8a0191d0bb9b
  source_type: agent
cognitive_type: episodic
source_type: agent
title: drift dashboard embedding options [5120bc32:10]
summary: "## User prompt\n\ndrift dashboard embedding options\noptions so far are an iframe per tool, the Superset embedded SDK, or a static exporter that rebuilds the dashboards on a schedule."
tags: []
---
# drift dashboard embedding options [5120bc32:10]

## User prompt

drift dashboard embedding options
options so far are an iframe per tool, the Superset embedded SDK, or a static exporter that rebuilds the dashboards on a schedule.

### Bash
tool_use_id: toolu_01dBVxdUgn0JRmaj3QnvyYMo
input: {"command": "drift-exporter --help"}
response: static rebuild, s3 target, 15m schedule
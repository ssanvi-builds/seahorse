---
id: 019e63be-3589-7548-83f9-709adfef9906
created_at: '2026-05-26T10:04:30.473606Z'
schema_version: 1.0.0
provenance:
  agent_id: unknown
  confidence: 1.0
  extraction_mode: skip
  model_used: null
  prompt_hash: null
  session_id: 13070188-424e-4cc6-93a1-7d10314ecb50
  source_type: agent
cognitive_type: episodic
source_type: agent
title: drift dashboard embedding options [13070188:10]
summary: "## User prompt\n\ndrift dashboard embedding options\noptions so far are an iframe per tool, the Superset embedded SDK, or a static exporter that rebuilds the dashboards on a schedule."
tags: []
---
# drift dashboard embedding options [13070188:10]

## User prompt

drift dashboard embedding options
options so far are an iframe per tool, the Superset embedded SDK, or a static exporter that rebuilds the dashboards on a schedule.

### Bash
tool_use_id: toolu_01an3gvCPClwGl5mQU9DsLaJ
input: {"command": "drift-exporter --help"}
response: static rebuild, s3 target, 15m schedule
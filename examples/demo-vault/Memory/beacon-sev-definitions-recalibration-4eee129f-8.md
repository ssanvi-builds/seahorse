---
id: 019eee90-9756-7c24-9e8b-024c670647cb
created_at: '2026-06-22T09:01:53.878042Z'
schema_version: 1.0.0
provenance:
  agent_id: unknown
  confidence: 1.0
  extraction_mode: skip
  model_used: null
  prompt_hash: null
  session_id: 4eee129f-081e-4d67-a281-acf6d0d5d72f
  source_type: agent
cognitive_type: episodic
source_type: agent
title: beacon sev definitions recalibration [4eee129f:8]
summary: "## User prompt\n\nbeacon sev definitions recalibration\nthe recalibration ties each sev to a response window instead of a team: 5m for sev-1, 15m for sev-2, the working day for sev-3."
tags: []
---
# beacon sev definitions recalibration [4eee129f:8]

## User prompt

beacon sev definitions recalibration
the recalibration ties each sev to a response window instead of a team: 5m for sev-1, 15m for sev-2, the working day for sev-3.

### Read
tool_use_id: toolu_01w24bl1kyq75hFTmlpBWhnn
input: {"file_path": "repos/beacon/config/sev.yaml"}
response: defs: team_scoped, proposal: response_time_scoped
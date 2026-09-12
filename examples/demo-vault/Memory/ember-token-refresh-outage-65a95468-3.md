---
id: 01a0193f-c202-7b9f-ab51-967f0411e471
created_at: '2026-08-19T09:00:03.970680Z'
schema_version: 1.0.0
provenance:
  agent_id: unknown
  confidence: 1.0
  extraction_mode: skip
  model_used: null
  prompt_hash: null
  session_id: 65a95468-439a-479e-ae9e-a47467bc7b7d
  source_type: agent
cognitive_type: episodic
source_type: agent
title: ember token refresh outage [65a95468:3]
summary: "## User prompt\n\nember token refresh outage\nsupport paged at 09:03 UTC: mobile clients stuck in a refresh loop, 401 rates climbing on the data API."
tags: []
---
# ember token refresh outage [65a95468:3]

## User prompt

ember token refresh outage
support paged at 09:03 UTC: mobile clients stuck in a refresh loop, 401 rates climbing on the data API.

### Bash
tool_use_id: toolu_01RszX34vc6dYu9W2wTNh9xV
input: {"command": "ember-audit refresh --rate --last 1h"}
response: refresh_loops: 2870, 401_rate: 12%, families_affected: 340
---
id: 019f8e37-5ed0-770e-8b4c-9c4457fa6bf6
created_at: '2026-07-23T09:03:41.264709Z'
schema_version: 1.0.0
provenance:
  agent_id: seahorse/claude-code
  confidence: 0.9
  extraction_mode: llm
  model_used: claude-sonnet-5
  prompt_hash: 09b2114a17a8af91bef3b3c161f3028bc544010191b4d60930913371b63c65c2
  session_id: 9dd0ed84-c183-435c-8875-23443887906d
  source_type: agent
cognitive_type: procedural
source_type: agent
title: How to roll back an Atlas deployment
summary: Roll back to the previous image tag — never rebuild under pressure.
tags: []
---
Roll back to the previous image tag — never rebuild under pressure.

1. Keep the previous image tag in the deploy manifest
2. `atlas-deploy rollback --to <tag>` (bakes the old image, no rebuild)
3. Watch the [[Atlas]] lag panel for one partition cycle
4. Re-open the deploy ticket with the observed reason
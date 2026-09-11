---
id: 019f8e41-5f2a-7945-9fd8-0df8031a5323
created_at: '2026-07-23T09:14:36.714756Z'
schema_version: 1.0.0
provenance:
  agent_id: seahorse/claude-code
  confidence: 0.9
  extraction_mode: llm
  model_used: claude-sonnet-5
  prompt_hash: 09b2114a17a8af91bef3b3c161f3028bc544010191b4d60930913371b63c65c2
  session_id: 34c61c45-4298-423a-b4ae-976c01247688
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
3. Watch the lag panel for one partition cycle
4. Re-open the deploy ticket with the observed reason
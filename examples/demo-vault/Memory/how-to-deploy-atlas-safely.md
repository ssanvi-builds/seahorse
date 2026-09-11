---
id: 019d6735-6655-7254-8a4e-1ab17ea866bf
created_at: '2026-04-07T09:10:46.101766Z'
schema_version: 1.0.0
provenance:
  agent_id: seahorse/claude-code
  confidence: 0.87
  extraction_mode: llm
  model_used: claude-sonnet-5
  prompt_hash: de2a0b7c392a737141d62add2910982ee4c93d6e7c972ba7cc0cb94abd25c8f4
  session_id: 452231b5-6c1f-4d00-9b47-4d3000b27656
  source_type: agent
cognitive_type: procedural
source_type: agent
title: How to deploy Atlas safely
summary: Canary-first deploy with the lag panel as the gate.
tags: []
---
Canary-first deploy with the lag panel as the gate.

1. Green build on main, changelog entry present
2. Canary 5% of consumers for 30 minutes
3. Watch the [[Atlas]] lag panel — p99 under 2s or roll back
4. Full rollout, then post the deploy note in the weekly sync
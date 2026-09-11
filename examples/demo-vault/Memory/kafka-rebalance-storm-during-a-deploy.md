---
id: 019f605d-809e-7627-9f46-ee448afdd0f0
created_at: '2026-07-14T11:22:48.350041Z'
schema_version: 1.0.0
provenance:
  agent_id: seahorse/claude-code
  confidence: 0.87
  extraction_mode: llm
  model_used: claude-sonnet-5
  prompt_hash: a8a4a67dbd4dd46212044c7090c1b3fc0918c37081691902305efa0bdad2b5bf
  session_id: 207e7cec-393b-424b-9a9c-03bd7139c3f7
  source_type: agent
valid_at: '2026-07-14T00:00:00Z'
cognitive_type: episodic
source_type: agent
title: Kafka rebalance storm during a deploy
summary: 'Traced the 06:12 lag spike to a rebalance storm: every consumer restart from the deploy reassigning the whole group.'
tags: []
---
Traced the 06:12 lag spike to a rebalance storm: every consumer restart from the deploy reassigning the whole group. Led to the static-membership decision.
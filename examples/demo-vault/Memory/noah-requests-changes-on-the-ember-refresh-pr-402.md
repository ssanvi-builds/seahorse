---
id: 019d76a9-8002-7e7b-a763-bd89ba0e14fe
created_at: '2026-04-10T09:11:53.090163Z'
schema_version: 1.0.0
provenance:
  agent_id: seahorse/claude-code
  confidence: 0.89
  extraction_mode: llm
  model_used: claude-sonnet-5
  prompt_hash: aa3c873692d41418ac053618b3564a381688892872f5aee0f50164c6c608b127
  session_id: 5ba03fc4-8022-448f-ba3a-f11ea7411dbf
  source_type: agent
valid_at: '2026-04-10T00:00:00Z'
cognitive_type: episodic
source_type: agent
title: Noah requests changes on the Ember refresh PR 402
summary: '[[Noah Bennett]] blocked the refresh serialization patch: the client-side retry budget would have tripled.'
tags: []
---
[[Noah Bennett]] blocked the refresh serialization patch: the client-side retry budget would have tripled. It landed only after the backoff curve changed.
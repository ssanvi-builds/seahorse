---
id: 01a019cc-8042-7548-a994-2fd26765ffd5
created_at: '2026-08-19T11:33:47.714471Z'
schema_version: 1.0.0
provenance:
  agent_id: seahorse/claude-code
  confidence: 0.87
  extraction_mode: llm
  model_used: claude-sonnet-5
  prompt_hash: 83cac3adcd57c7a355d23d06af075b676835b626bef234e8df5d7f36c503dc59
  session_id: 2d465835-667d-4fb6-b4a9-21371429f750
  source_type: agent
valid_at: '2026-08-19T00:00:00Z'
cognitive_type: episodic
source_type: agent
title: Ember refresh loop root cause
summary: 'The refresh outage was a missing per-family lock after a deploy: winner and loser invalidated each other, so every client retried in a loop.'
tags: []
---
The refresh outage was a missing per-family lock after a deploy: winner and loser invalidated each other, so every client retried in a loop. Fixed with the deploy-guard lock assertion.
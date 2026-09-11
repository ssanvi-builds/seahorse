---
id: 019e1b73-95b6-719f-b857-dd95f3b5b781
created_at: '2026-05-12T09:10:20.342300Z'
schema_version: 1.0.0
provenance:
  agent_id: seahorse/claude-code
  confidence: 0.95
  extraction_mode: llm
  model_used: claude-sonnet-5
  prompt_hash: 2d2b6a3a6b7f742d08e01e49604c983357e780e72a1cb87d2c5f022813e1a164
  session_id: 2cc90e37-5d04-43b8-844a-bcc8be933882
  source_type: agent
cognitive_type: procedural
source_type: agent
title: How to replay an Atlas topic into Iceberg
summary: Dry-run first; the dedup report is the contract.
tags: []
---
Dry-run first; the dedup report is the contract.

1. `atlas-replay --topic orders --from <offset> --dry-run`
2. Check the dedup report the tool prints (exactly-once does the rest)
3. Run without `--dry-run`, then compare row counts in Iceberg
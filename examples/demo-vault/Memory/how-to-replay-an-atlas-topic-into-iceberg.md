---
id: 019e1b7b-40f0-7595-87c6-20fc0f8296c6
created_at: '2026-05-12T09:18:42.928977Z'
schema_version: 1.0.0
provenance:
  agent_id: seahorse/claude-code
  confidence: 0.89
  extraction_mode: llm
  model_used: claude-sonnet-5
  prompt_hash: 2d2b6a3a6b7f742d08e01e49604c983357e780e72a1cb87d2c5f022813e1a164
  session_id: 34331f8c-d5d9-481e-a86e-a90e08398fa2
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
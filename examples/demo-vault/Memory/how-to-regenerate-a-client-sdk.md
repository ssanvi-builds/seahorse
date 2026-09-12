---
id: 019fff90-8750-7d88-b656-928984ed43f6
created_at: '2026-08-14T09:18:09.744593Z'
schema_version: 1.0.0
provenance:
  agent_id: seahorse/claude-code
  confidence: 0.92
  extraction_mode: llm
  model_used: claude-sonnet-5
  prompt_hash: d1707c8f1066b2fbb292fc5a69783226af02d5dd97dd9c012312f547fcd8421b
  session_id: a2c31198-0fe9-488d-b4c8-8eb1d4d2b974
  source_type: agent
cognitive_type: procedural
source_type: agent
title: How to regenerate a client SDK
summary: Generated only; the spec in CI is the single source.
tags: []
---
Generated only; the spec in CI is the single source.

1. Confirm the spec version on the data API release page
2. `make sdk VERSION=<v>` — the generator reads the CI spec, not a copy
3. Diff the public surface; any manual edit you find is a bug
4. Ship to client teams with the sunset date of the version it replaces
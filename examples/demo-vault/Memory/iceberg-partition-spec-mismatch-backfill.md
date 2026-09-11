---
id: 019f0337-5335-7a34-afab-dd07e5840dbc
created_at: '2026-06-26T09:16:25.269409Z'
schema_version: 1.0.0
provenance:
  agent_id: seahorse/claude-code
  confidence: 0.97
  extraction_mode: llm
  model_used: claude-sonnet-5
  prompt_hash: 301f30814b8b9ae2b99e57ac42b2e773aedc4b4f8c86afaa0839816686bf8507
  session_id: bf9ddf19-5d76-4a06-bf8b-93ab0b948b9a
  source_type: agent
valid_at: '2026-06-26T00:00:00Z'
cognitive_type: episodic
source_type: agent
title: Iceberg partition spec mismatch backfill
summary: A table rewritten with a new partition spec rejected the old files' manifests.
tags: []
---
A table rewritten with a new partition spec rejected the old files' manifests. The backfill re-registered the manifests; the spec change now ships with a migration note.
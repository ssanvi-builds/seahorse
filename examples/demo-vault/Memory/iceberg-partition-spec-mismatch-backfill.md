---
id: 019f0329-2a38-729f-9a7b-61ccf4d0db21
created_at: '2026-06-26T09:00:57.272082Z'
schema_version: 1.0.0
provenance:
  agent_id: seahorse/claude-code
  confidence: 0.94
  extraction_mode: llm
  model_used: claude-sonnet-5
  prompt_hash: 301f30814b8b9ae2b99e57ac42b2e773aedc4b4f8c86afaa0839816686bf8507
  session_id: 4c00c034-b249-4fd1-a6f3-dc0186394a68
  source_type: agent
valid_at: '2026-06-26T00:00:00Z'
cognitive_type: episodic
source_type: agent
title: Iceberg partition spec mismatch backfill
summary: A table rewritten with a new partition spec rejected the old files' manifests.
tags: []
---
A table rewritten with a new partition spec rejected the old files' manifests. The backfill re-registered the manifests; the spec change now ships with a migration note.
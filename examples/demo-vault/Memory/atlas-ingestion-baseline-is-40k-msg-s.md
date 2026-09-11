---
id: 019eaba1-4a3f-7127-9cd9-56ddd6cd032b
created_at: '2026-06-09T09:05:34.783708Z'
schema_version: 1.0.0
provenance:
  confidence: 1.0
  extraction_mode: skip
  model_used: null
  prompt_hash: null
  source_type: human
valid_at: '2026-06-09T00:00:00Z'
supersedes: 019c6ffa-95a4-7cad-bd99-60d1b9462d01
supersedes_reason: correction
source_type: human
tags: []
---
# Atlas ingestion baseline is 40k msg/s

The exactly-once tuning plus the partition re-balance raised the sustained baseline from 30k to 40k msg/s; p99 lag stays under 2s.
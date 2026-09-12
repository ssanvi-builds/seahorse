---
id: 019eaba8-419a-7702-8fa4-4a1237a8da5a
created_at: '2026-06-09T09:13:11.322134Z'
schema_version: 1.0.0
provenance:
  confidence: 1.0
  extraction_mode: skip
  model_used: null
  prompt_hash: null
  source_type: human
valid_at: '2026-06-09T00:00:00Z'
supersedes: 019c7008-8662-7f75-a470-9501c11ebde1
supersedes_reason: correction
source_type: human
tags: []
---
# Atlas ingestion baseline is 40k msg/s

The exactly-once tuning plus the partition re-balance raised the sustained baseline from 30k to 40k msg/s; p99 lag stays under 2s.
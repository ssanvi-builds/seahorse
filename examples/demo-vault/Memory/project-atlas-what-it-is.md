---
id: 019be4fa-d7fe-7221-a803-eaff26333e2a
created_at: '2026-01-22T09:13:23.198163Z'
schema_version: 1.0.0
provenance:
  agent_id: seahorse/claude-code
  confidence: 0.94
  extraction_mode: llm
  model_used: claude-sonnet-5
  prompt_hash: 484bce2cf2a3bdbe84e428eab4209ee9bd1deb5ccc64db68c28639328df998d1
  session_id: d7fe3097-cd04-4ae7-9e43-7f76a9181528
  source_type: agent
cognitive_type: project_doc
source_type: agent
title: 'Project Atlas: what it is'
summary: 'Atlas is the ingestion platform: Kafka topics land as Iceberg tables on S3.'
tags: []
---
Atlas is the ingestion platform: Kafka topics land as Iceberg tables on S3. Exactly-once semantics are under evaluation; ingest sustains 30k msg/s today. The consumer group is owned by [[Daniel Okafor]].
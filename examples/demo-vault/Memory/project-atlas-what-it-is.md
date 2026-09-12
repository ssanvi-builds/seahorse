---
id: 019be4f9-6fe3-762a-ad82-6c1e433ad4f8
created_at: '2026-01-22T09:11:51.011091Z'
schema_version: 1.0.0
provenance:
  agent_id: seahorse/claude-code
  confidence: 0.89
  extraction_mode: llm
  model_used: claude-sonnet-5
  prompt_hash: 484bce2cf2a3bdbe84e428eab4209ee9bd1deb5ccc64db68c28639328df998d1
  session_id: 2334ddad-334a-4e0d-91ca-70679a0c3d68
  source_type: agent
cognitive_type: project_doc
source_type: agent
title: 'Project Atlas: what it is'
summary: 'Atlas is the ingestion platform: Kafka topics land as Iceberg tables on S3.'
tags: []
---
Atlas is the ingestion platform: Kafka topics land as Iceberg tables on S3. Exactly-once semantics are under evaluation; ingest sustains 30k msg/s today. The consumer group is owned by [[Daniel Okafor]].
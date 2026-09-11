---
id: 019d1f45-e7a8-7150-8049-cbf568d24545
created_at: '2026-03-24T09:56:08.232075Z'
schema_version: 1.0.0
provenance:
  agent_id: unknown
  confidence: 1.0
  extraction_mode: skip
  model_used: null
  prompt_hash: null
  session_id: dba17c32-9c8b-41d9-97bf-fa38be1ccd47
  source_type: agent
cognitive_type: episodic
source_type: agent
title: atlas retention policy discussion [dba17c32:12]
summary: "## User prompt\n\natlas retention policy discussion\nfinal shape: 21 days hot plus a quarterly archive tier, and the replay tool needs the archive offsets documented."
tags: []
---
# atlas retention policy discussion [dba17c32:12]

## User prompt

atlas retention policy discussion
final shape: 21 days hot plus a quarterly archive tier, and the replay tool needs the archive offsets documented.

### Read
tool_use_id: toolu_01Bl1xyZwRHTFnMf50K46etL
input: {"file_path": "repos/atlas/config/retention.yaml"}
response: retention: 21d, archive: quarterly

### Bash
tool_use_id: toolu_01HLaXvy6YzLMAVhCMytNGv5
input: {"command": "atlas-replay --help"}
response: --from accepts hot or archive offsets
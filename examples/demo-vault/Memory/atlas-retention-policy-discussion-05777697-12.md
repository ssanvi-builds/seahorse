---
id: 019d1f4c-1f6a-71cd-81e1-c7fe18584f8f
created_at: '2026-03-24T10:02:55.722184Z'
schema_version: 1.0.0
provenance:
  agent_id: unknown
  confidence: 1.0
  extraction_mode: skip
  model_used: null
  prompt_hash: null
  session_id: 05777697-a1ea-4dfb-9b4f-d2fc681ad057
  source_type: agent
cognitive_type: episodic
source_type: agent
title: atlas retention policy discussion [05777697:12]
summary: "## User prompt\n\natlas retention policy discussion\nfinal shape: 21 days hot plus a quarterly archive tier, and the replay tool needs the archive offsets documented."
tags: []
---
# atlas retention policy discussion [05777697:12]

## User prompt

atlas retention policy discussion
final shape: 21 days hot plus a quarterly archive tier, and the replay tool needs the archive offsets documented.

### Read
tool_use_id: toolu_019JtgqoYd8kSb7TySRDH6K5
input: {"file_path": "repos/atlas/config/retention.yaml"}
response: retention: 21d, archive: quarterly

### Bash
tool_use_id: toolu_01QzwfT78Ztz9bbGRI1cBCfu
input: {"command": "atlas-replay --help"}
response: --from accepts hot or archive offsets
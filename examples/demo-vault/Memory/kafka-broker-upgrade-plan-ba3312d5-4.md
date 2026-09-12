---
id: 019f12ac-6464-7205-95c2-0aa9abf3b3e3
created_at: '2026-06-29T09:18:35.620375Z'
schema_version: 1.0.0
provenance:
  agent_id: unknown
  confidence: 1.0
  extraction_mode: skip
  model_used: null
  prompt_hash: null
  session_id: ba3312d5-57fa-4561-af56-bf84458ff43e
  source_type: agent
cognitive_type: episodic
source_type: agent
title: kafka broker upgrade plan [ba3312d5:4]
summary: "## User prompt\n\nkafka broker upgrade plan\nthe plan is two weekend windows with static membership verified first, and a rollback image per broker."
tags: []
---
# kafka broker upgrade plan [ba3312d5:4]

## User prompt

kafka broker upgrade plan
the plan is two weekend windows with static membership verified first, and a rollback image per broker.

### Read
tool_use_id: toolu_015fq8X1KnCXgwhC2T8lK4Sp
input: {"file_path": "repos/atlas/runbooks/broker-upgrade.md"}
response: windows: 2, prerequisite: static_membership, rollback: image_tag
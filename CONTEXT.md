# Seahorse

Persistent, self-evolving memory for LLM agents: an open standard where an agent's
knowledge lives in auditable markdown (Obsidian notes) with a bi-temporal engine,
a hybrid retrieval path, and a consolidation loop that turns episodes into
durable facts.

## Language

### Time

**valid_at**:
The instant a fact became true in the world (as distinct from when the system
learned it).
_Avoid_: start date, effective time

**invalid_at**:
The instant a fact stopped being true; null while it remains valid. Set by
soft-delete, never by deletion.
_Avoid_: deleted_at, end date

**created_at**:
The instant the system recorded the fact — when it was known, not when it was
true.
_Avoid_: written_at, timestamp

**expired_at**:
The instant a fact decayed out of use by the forgetting curve; distinct from
`invalid_at` (obsolete is not false, just unused).
_Avoid_: forgotten_at, pruned_at

**Point-in-time query**:
A read that reproduces the knowledge base as of a past instant (`valid_at <= t`
and not-yet-invalid), routing on one time axis at a time.
_Avoid_: snapshot, historical read

### Memory layers

**Episode**:
The atomic unit of episodic memory: a single recorded happening, rich in detail,
tied to a `session_id` and a time.
_Avoid_: event, log entry, record

**Episodic memory**:
What happened — the per-session journal, perishable by nature and compressed
into facts over time.
_Avoid_: history, transcript

**Semantic memory**:
What we know — consolidated facts independent of when they were learned.
_Avoid_: knowledge base, store

**Fact**:
A durable, consolidated unit of semantic knowledge produced from one or more
episodes.
_Avoid_: record, entry, row

**Skill**:
A unit of procedural memory: a repeatable how-to recipe, authored
deterministically and gated by a trust level.
_Avoid_: recipe, prompt, tool

### Lifecycle operations

**Supersession**:
Replacing a fact's body with a corrected one, append-only — the old body is
retained as history and the new one carries the supersession reason.
_Avoid_: update, overwrite, edit

**Soft-delete**:
Invalidating a fact by setting `invalid_at`, never destroying its history.
_Avoid_: delete, remove, purge

**Consolidation**:
Distilling many episodes into one coherent fact, offline and off the latency
path.
_Avoid_: summarization, dedup

**Materialization**:
Writing a fact out as an auditable markdown note in the vault's `Memory/`
folder, editable by the human.
_Avoid_: render, export, sync

**Recall**:
The retrieval operation; by default returns the compressed index and only
hydrates bodies on demand.
_Avoid_: search, lookup, query

### Retrieval

**Progressive disclosure**:
Retrieval in three levels — index, timeline, full — so the agent pays for
detail only on the few results worth it.
_Avoid_: pagination, lazy loading

**Hybrid retrieval**:
Ranking by fusing vector (semantic) and keyword (FTS/BM25) results with
Reciprocal Rank Fusion, no LLM in the query loop.
_Avoid_: RAG, vector search

**Decay**:
An opt-in forgetting curve that down-weights a fact by its `created_at` age.
_Avoid_: expiry, staleness

### Provenance

**Provenance**:
The block recording who produced a fact and how — `agent_id`, `session_id`,
`source_type`, `extraction_mode`, `model_used` — the basis for audit and
selective revert.
_Avoid_: metadata, attribution

**Source type**:
Who authored a fact: `agent`, `human`, `importer`, or `system`.
_Avoid_: author, origin

**Extraction mode**:
How a write distilled its body: the deterministic `skip` path, or the `llm`
path (with its model and prompt hash).
_Avoid_: write mode, generation path

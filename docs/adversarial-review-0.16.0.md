# Adversarial review — materialization + editorial distillation (0.16.0)

Adversarial review of the 0.16.0 design (episodes → `.md` materialization +
editorial distillation) before implementation. Each finding carries a verdict:
**resolved** (the design/implementation addresses it) or **accepted** (documented
trade-off for the MVP). This document is attached to the feature PR.

## Critical

### C1 — Invalidation does not propagate to `.md` → rebuild resurrects invalidated episodes

`forget` / `improve` set `invalid_at` in the engine (`episodes` + `episode_index`)
but leave the materialized `.md` untouched. A later `seahorse index rebuild`
re-parses the `.md` (whose frontmatter still has `invalid_at=None`) and re-indexes
the episode as currently-valid → the invalidated episode is resurrected.

**Verdict: resolved.** The materializer owns the `.md` lifecycle end-to-end:
- `materialize(ep_id)` writes the `.md` on an ACTIVE write (remember / distill /
  improve successor).
- `invalidate(ep_id)` is a new facade hook on `forget` and `improve` (the old
  episode): it re-reads the materialized `.md`, merges the episode's updated
  frontmatter (`invalid_at` set) onto the baseline, and preserves the current
  body (a human body edit survives — the merge only touches changed schema
  fields). The rebuild then reads `invalid_at` from the `.md` and indexes the
  episode as invalid — no resurrection, no rebuild-path change.

The rebuild already handles invalidated notes correctly: `_conflict_group_ids`
excludes notes with `invalid_at`/`expired_at` set (sqlite_sidecar.py:212) and
`_REBUILD_INDEX_INSERT` stores `invalid_at` (sqlite_sidecar.py:168).

### C2 — Consolidated `.md` derives a different `fact_id` than the engine on rebuild

`distill_episodes` writes the consolidated episode with `title=representative.subject`
(which carries the `[session_tag:n]` suffix) but `subject=cluster_key(...)` (the
stable key, no suffix) — distill.py:87-89. The rebuild's `_parsed_note` derives
subject from `raw_subject(title, body)` — title first — so it derives the
suffixed subject → a different `fact_id` than the engine → phantom conflicts.

**Verdict: resolved.** The materializer serializes an *effective episode* whose
`title` is the stable `subject` for consolidated episodes
(`extraction_mode=consolidated`); all other episodes keep their engine title. The
rebuild then derives subject from the stable title → `fact_id` matches the
engine. The `.md` title may differ from the engine's title for consolidated
notes (the engine's title carries the session tag; the `.md` title is the clean
stable key — better for humans). The same effective episode is used by
`invalidate` so an invalidation merge never clobbers the stable title back to the
suffixed one.

### C3 — `mtime > created_at` guard fires against seahorse's own `.md` → backfill duplicates notes

The plan's human-edit guard (`mtime > created_at`) cannot work for the
materializer: the `.md` is written *after* `created_at`, so `mtime > created_at`
is always true for seahorse's own notes → backfill would treat every note as
human-edited and duplicate it.

**Verdict: resolved.** The guard compares the **frontmatter id** of the existing
`.md` with the episode id, not mtimes:
- existing `.md` id == episode id → seahorse's own materialization → skip
  (idempotent backfill; episodes are append-only, so the `.md` is current).
- existing `.md` id != episode id → a human note or another episode → never
  overwrite; write to `{slug}-{id8}.md` (collision suffix).

**Second manifestation (found in e2e-loop, 2026-09-01):** the consolidate
editorial-authority guard (`_vault_human_edited`, cli/primitives.py) used the
same `mtime > created_at` heuristic — and the materializer writes its own
`.md` AFTER `created_at`, so `consolidate --supersede` treated every
materialized note as human-edited and never superseded. Fixed the same way:
the guard now reads the `.md` frontmatter id and skips seahorse's own notes
(id match); only a `.md` with a different or absent id can be human-touched.

### C4 — Guard fails in supersession: a human edit of the old `.md` gets overwritten

With the mtime guard, supersession compares the old `.md` against the *new*
episode's `created_at` — always newer → the human edit is clobbered.

**Verdict: resolved.** Supersession is handled by `invalidate` (C1), which is a
merge, not an overwrite: the current body (including a human edit) is preserved
and only `invalid_at` is added. The materialization guard (C3) never overwrites
a `.md` with a different id.

### C5 — Supersession produces two `.md` with the same subject → rebuild treats both as a conflict

The old and new episodes share a subject → two `.md` with the same slug → the
rebuild's duplicate-vigent detection skips both.

**Verdict: resolved.** Consequence of C1: the old `.md` carries `invalid_at` →
excluded from the conflict group (sqlite_sidecar.py:212) → only the successor is
indexed as vigente. The successor's `.md` gets the `{slug}-{id8}.md` collision
suffix (C3).

## Major

### M1 — Human `.md` edit is not reconciled with `episodes` → recall returns stale bodies

A human edits a materialized `.md`; the engine's `episodes` table still holds the
original body. Hot-path recall returns the original; rebuild-based recall returns
the edited body.

**Verdict: accepted for the MVP.** Documented in `docs/editorial-notes.md`: the
`.md` is the human-facing materialization; the engine's `episodes` table is the
hot-path store. Full reconciliation (`episodes` ← edited `.md`) is out of scope
(already the case today for human-authored notes). The rebuild is the operator's
reconciliation tool.

### M2 — `skip_extraction` diverges between the hot path and the rebuild

The hot path hardcodes `skip_extraction=0` for every episode
(sqlite_episode_repo.py:122); the rebuild derives `1` for `extraction_mode=skip`
(sqlite_sidecar.py:66-71). Materialization makes the divergence visible (the
rebuild now re-parses seahorse's own `.md`).

**Verdict: resolved.** Unify the derivation: the hot path derives
`skip_extraction` from `extraction_mode` (1 for `skip`, 0 otherwise) — the same
rule as the rebuild. The rebuild's rule matches the documented contract
("`skip_extraction=1` excludes from the FTS5 + embedding queue"): a skip-path
episode has no LLM extraction, so nothing to embed.

### M3 — Default `Memory` folder can collide with legacy user notes

A user with an existing `Memory/` folder could see the materializer write into
it; a same-subject legacy note and a materialized note would trip the rebuild's
duplicate-vigent detection.

**Verdict: resolved (documented).** The `dir` is configurable (`[materialize]
dir`); the default `Memory` is a visible, dedicated folder. The materializer's
collision handling (C3) never overwrites a foreign note, and the rebuild reports
conflicts instead of silently picking a winner — the operator resolves. The
setup wizard asks for the vault and writes the section; the docs recommend a
dedicated folder.

### M4 — Episodes with `subject=None` are not covered

A note with neither title nor H1 has `subject=None` → no slug to name the `.md`.

**Verdict: resolved.** The materializer skips `subject=None` episodes and reports
them (`reason="no_subject"`). They are degenerate for materialization (a
project_doc note always carries a descriptive subject; a skip-path episode
without a subject is not knowledge worth surfacing).

### M5 — Slug sanitization is unspecified

Non-ASCII subjects, invalid filesystem characters, empty slugs.

**Verdict: resolved.** An explicit `slugify` in the materializer: lowercase,
keep `[a-z0-9]`, collapse runs of other characters to a single `-`, strip
leading/trailing `-`. An empty result falls back to the `id8` prefix. The slug
is a filename, not a display name — the frontmatter `title`/`subject` carry the
human-readable form.

### M6 — Materializer injection point for the distill path is unspecified

`distill_episodes` is a pure module function without `vault_root`/`sidecar`.

**Verdict: resolved.** The materializer is injected at the **facade** (a
`materializer` param on `MemoryFacade`, same pattern as `on_episode_indexed`),
not in the write path or `distill_episodes`. The facade calls it after all four
write surfaces — `remember` (ACTIVE), `distill`, `improve` (successor +
invalidate old), `forget` (invalidate) — so the hook is one injection point that
covers the write path, the distill bypass, and the invalidation paths (C1). The
facade fetches the episode via a new public `engine.get(ep_id)` reader (the repo
already has `get`; the engine uses it internally but does not expose it).

### M7 — Backfill and `PENDING_INGEST`

`query_vigent` includes PENDING episodes; `get_vigente` post-filters
`valid_at <= now`. A backfill that used the raw query would materialize
not-yet-valid episodes.

**Verdict: resolved.** Backfill (`seahorse materialize`) uses the `get_vigente`
predicate (currently-valid, `valid_at <= now`). PENDING episodes are not
materialized until they become vigente.

### M8 — `.md` exposes provenance metadata in a synced vault

The F3.1 frontmatter carries `provenance` (agent_id, session_id,
source_record_id) — visible to anyone with vault access.

**Verdict: accepted for the MVP.** The F3.1 format requires provenance in the
frontmatter (it is the format's contract). Documented in
`docs/editorial-notes.md`: users who sync the vault should be aware the
frontmatter carries provenance; the body is the human-facing content.

### M9 — Synchronous `.md` write on every `remember` (mode=all)

A synchronous filesystem write per episode adds latency to the write path.

**Verdict: accepted with an explicit best-effort contract.** The materializer
never fails the write: exceptions are caught, logged, and reported — the episode
lives in SQLite regardless. `seahorse materialize` is the backfill for anything
missed. Async materialization is out of scope for the MVP.

## Minor

- **m1 — Nested `provenance` renders collapsed in Obsidian.** Accepted. Obsidian
  renders nested YAML as collapsed properties; the body is the primary content.
- **m2 — Three titles for consolidated notes.** Resolved with C2 (the `.md`
  title is the stable subject; the body H1 is the stable key; the engine title
  keeps the session tag).
- **m3 — Moving a `.md` breaks `episode_paths` until rebuild.** Accepted.
  Documented: run `seahorse index rebuild` after reorganizing the vault.
- **m4 — `id8` collision suffix has a ~2^16 birthday bound.** Accepted. A
  collision on the suffixed name is reported (never silently overwritten); the
  operator resolves.
- **m5 — `episode_index.file_path` stays NULL after materialization.** Accepted.
  The hot path leaves `file_path` NULL (it is a rebuild-owned column); the
  materializer registers the path in `episode_paths` (the authoritative path
  store) and the rebuild fills `file_path`.

## Design decisions the implementation follows

1. The materializer owns the `.md` lifecycle: write on ACTIVE, invalidate on
   forget/improve (C1).
2. The human-edit guard compares frontmatter ids, not mtimes (C3/C4).
3. Consolidated `.md` title = stable subject (C2).
4. The materializer is a facade-level hook (M6).
5. `slugify` is explicit; `subject=None` episodes are skipped (M4/M5).
6. The default `dir` is `Memory` (configurable); collisions are reported, never
   silently resolved (M3).
7. `skip_extraction` derivation is unified between the hot path and the rebuild
   (M2).
8. Backfill uses the `get_vigente` predicate (M7).

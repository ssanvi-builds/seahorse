# Editorial notes — the agent's project memory, visible and editable

The editorial distillation pattern closes the loop between the agent's memory
and the human's vault: the agent writes **project notes** via the MCP `remember`
tool, Seahorse indexes them and materializes them as F3.1 `.md` files in the
vault, and `recall` finds them again. The result is a vault like the ones a
human already keeps — distilled knowledge notes that are visible, editable, and
synchronizable in Obsidian.

This is the product experience of Seahorse: the agent works with the same
notes the human reads and edits.

---

## 1. The pattern in one paragraph

1. **The agent writes** a project note: `remember` with
   `cognitive_type=project_doc`, a descriptive `subject`, and a body with an H1.
2. **Seahorse indexes it** (write path → `episode_index`) and **materializes it**
   as an F3.1 `.md` note in the vault (the `[materialize] dir`, default
   `Memory/`).
3. **The agent loads context**: `recall(query, cognitive_type="project_doc")`
   finds the note, `recall_full(ep_ids)` hydrates the body.
4. **The human organizes**: moves the `.md` into `Claude/`, `Roadmap/`,
   `Projects/`, or any folder; `seahorse index rebuild` picks the note up from
   its new path.

The note is the shared artifact: the agent writes it, the human edits it, and
the rebuild respects the edit.

---

## 2. Writing a project note (the agent)

A project note is a `remember` call with `cognitive_type=project_doc`. The
subject is the stable key the note is named by; the body carries the content.

```json
{
  "tool": "remember",
  "input": {
    "body": "# Seahorse 0.16.0 status\n\nMaterialization is done. Hooks wired. Next: release.",
    "by": { "source_type": "agent", "agent_id": "claude", "session_id": "s-2026-09-01" },
    "cognitive_type": "project_doc"
  }
}
```

Guidelines:

- **`subject`** is derived from the title / first H1 (title > H1 > none). A
  descriptive H1 gives the note a stable, human-readable name
  (`seahorse-0-16-0-status.md`).
- **One topic per note.** The note is the unit the agent recalls and the human
  edits. A note that mixes topics is hard to find and hard to supersede.
- **The body is the content.** The frontmatter is metadata; the human reads and
  edits the body.

The write path materializes the note automatically when the `[materialize]`
mode includes `project_doc` (the default `consolidated` mode does).

---

## 3. Loading context (the agent)

"¿En qué punto estamos?" — the agent asks, and loads the project notes:

1. `recall(query, cognitive_type="project_doc")` — the INDEX level: the current
   project notes, ranked by the configured retrieval.
2. `recall_full(ep_ids)` — the FULL level: hydrate the bodies of the top notes.
3. Read the notes as context.

The `cognitive_type` filter is the seam: project notes are the distilled
knowledge the agent works with, not the session noise. No ranking boost is
needed in the MVP — the filter is enough.

---

## 4. Organizing the vault (the human)

The materialized notes land in the `[materialize] dir` (default `Memory/`).
The human is free to move them:

- `Claude/` — the agent's working notes.
- `Roadmap/` — project direction.
- `Projects/` — per-project knowledge.

`seahorse index rebuild` re-discovers every `.md` in the vault from any path,
so a moved note is re-indexed at its new location. Run it after reorganizing
the vault (a moved note's registered path in `episode_paths` is stale until the
rebuild).

---

## 5. The materialization contract

- **The frontmatter is F3.1** (see `docs/f3.1-format.md`). In Obsidian it
  renders as collapsed properties — the body is the primary content.
- **A human body edit survives.** The materializer never overwrites a note with
  a different frontmatter id (the human-edit guard compares ids, not mtimes).
  `forget` / `improve` invalidate the note by *merging* `invalid_at` into the
  frontmatter, preserving the current body.
- **The rebuild respects the edit.** A note with `invalid_at` set is excluded
  from the current-state set — no resurrection.
- **Collisions are reported, never silently resolved.** A same-subject note that
  is not ours gets the `{slug}-{id8}.md` suffix; a conflict on the suffixed
  name is reported for the operator to resolve.

---

## 6. Known trade-offs (MVP)

- **The engine's `episodes` table is the hot-path store.** A human edit to the
  `.md` is not reconciled back into `episodes` until a rebuild. The rebuild is
  the operator's reconciliation tool (this is already the case for
  human-authored notes).
- **The frontmatter carries provenance** (`agent_id`, `session_id`,
  `source_record_id`). Users who sync the vault should be aware the frontmatter
  exposes it; the body is the human-facing content.
- **Materialization is synchronous and best-effort.** A failed write is logged
  and reported; the episode lives in SQLite regardless. `seahorse materialize`
  is the backfill for anything missed.

---

## 7. Enabling materialization

Materialization is opt-in. `seahorse setup` writes the `[materialize]` section
with the defaults; `seahorse materialize` backfills notes for the currently-valid
episodes.

```toml
[materialize]
mode = "consolidated"   # consolidated | all | off
dir = "Memory"          # vault-relative folder for the notes
```

- `consolidated` (default) — distilled knowledge (`extraction_mode=consolidated`)
  and project notes (`cognitive_type=project_doc`).
- `all` — every currently-valid episode.
- `off` — no materialization.

# Adversarial review — v1.4.0 ("human-edit honesty") before publishing

Adversarial review of the v1.4.0 sprint (`git diff 36dde35..HEAD`: 26 files,
+1940/−515, 7 commits `21bdec8..28890d7`) before publishing. Five critics ran
in parallel over the diff + tree — never over commit messages — each with its
own attack list (the `recall()` refactor, the F3.2 interop field, claims
honesty, divergence/doctor robustness, packaging/release). Every claim was
re-verified against the code before it counted; refuted ones are not listed.

Each finding carries a verdict: **resolved** (fixed in this review cycle, TDD)
or **accepted** (documented trade-off). Severity as filed: 0 CRITICAL,
2 HIGH, 4 MEDIUM, 5 LOW — 9 resolved, 2 accepted.

## Gates re-run on the fixed tree

- Full suite: 3055 passed / 9 skipped (exit 0), coverage gate ≥80% green.
- `ruff check .`: clean. `mypy src`: clean (208 files).
- Fingerprint A/B (corpus fijo, `benchmark experiment recency --retrieval-only`,
  HashEmbedder + StubReaderLLM): zero ranking diffs `36dde35` vs HEAD across
  10 manifests — the `recall()` stage refactor moved no ranking. Diffs limited
  to the known-nondeterministic fields (`started_at`, `run_id`, `config_hash`
  — output-dir-path-dependent — and `latency_p95_ms`), excluded by comparator.
- e2e: harnesses 47/47 checks, fresh-user 62/62 checks.

## High

### H1 — CHANGELOG misdescribed the lazy-recall metric

The `[1.4.0]` bullet claimed the harness "separates index-cache recall from
live vault parse in its accounting" — nothing of the sort is shipped. The real
experiment (`src/seahorse/benchmark/experiments/lazy_recall.py`) measures,
over captured observer envelopes, per task `(session_id, prompt_number)`, the
share of tasks with ≥1 recall call (the call/skip rate) and the share where
the call returned at least one row into context. It is the entry criterion for
a task-scoped read gate, not an accounting split.

**Verdict: resolved.** The bullet now states the real contract, and the
module docstring gains the floor caveat: both rates are floors, never
ceilings — a hook that serializes the response in an unrecognized shape, or
redaction acting on the envelope, can only lower a rate, never inflate one.
The `[1.4.0]` intro line was corrected to match ("the benchmark harness
measures the recall call/skip rate over observer traces").

### H2 — `facade.improve` drops `derived_from` membership (the F3.2 decision was unimplemented)

`f3-2-derived-from-field-shape.md` §8 says a consolidated note corrected with
`improve` "hereda la membresía que no cambia y añade la nueva evidencia" — but
`improve` (`src/seahorse/facade/facade.py`) built the effective provenance
with only the regime marker (`extraction_mode='consolidated'`). The successor
lost the cluster membership entirely: the emitted `x-seahorse-derived-from`
shrank to the superseded note's own `supersedes` edge, so consumers had to
walk the supersedes chain after all — the exact job the field exists to spare
them. Repro: consolidated note with `derived_from=[{ep-a, evidence},
{ep-b, evidence}]`, `improve` → successor's effective provenance carries no
`derived_from` key at all.

**Verdict: resolved.** `improve` now inherits the membership when the old
episode is a consolidated note (`cognitive_type=semantic` +
`extraction_mode=consolidated`), via a tolerant parse shared with the distill
path: self-referencing edges are dropped, the lineage closes with
`{ep_id, supersedes}`, a caller-supplied `by.derived_from` wins, no key is
invented when the old note carries no membership (and no closing edge on an
empty parse). The regime-inheritance docstring paragraph gains the membership
rationale. TDD: `tests/facade/test_improve.py::TestImproveInheritsMembership`
(6 tests: inherits+closes, caller wins, no-membership → no key, non-
consolidated → no membership, unknown `edge_kind` preserved, malformed
tolerated).

## Medium

### M1 — `_derived_from` crashes on malformed inherited membership

The supersession loop (`src/seahorse/distill/distill.py`, `_derived_from`)
called `edge.get("id")` on every inherited entry. A plain string entry (the
minimal third-party membership shape — a real design alternative) or an int
raises `AttributeError` → a whole `consolidate` run dies on a hand-edited or
third-party provenance block. Repro: supersede an episode whose provenance
carries `derived_from: ["ep-x", 7]` → crash.

**Verdict: resolved.** New `normalize_derived_from(raw)` (module-level, same
module) parses tolerantly, mirroring the `x-*` preserve-never-reject policy:
dicts with a string `id` pass through verbatim (unknown `edge_kind`
preserved), plain strings are promoted to `evidence`, unusable entries are
skipped with a logged warning. NO dedupe inside — callers own dedupe (they
hold the known-set). Used by `_derived_from`'s supersession loop and by the
facade inheritance. Test:
`tests/distill/test_derived_from.py::test_supersession_tolerates_malformed_inherited_membership`.

### M2 — "PIT never reranked" claim is false (rerank is not PIT-gated)

`engine.py`'s stage comment, the CHANGELOG and ROADMAP claimed PIT queries
are never "boosted/decayed/reranked", but `_apply_rerank_stage` takes no
`pit` argument and `HybridRetriever` wires the reranker unconditionally. The
ADR-03 nuance: recency/decay are anachronistic (time-dependent) biases →
PIT-gated; rerank is time-neutral (a cross-encoder relevance reorder of the
as-of-t set — the set itself is unchanged) → no gate is required. The
implementation is correct; the texts overclaimed.

**Verdict: resolved (claims fix).** All four mentions corrected: the
`engine.py` stage comment now claims only boosted/decayed with the
ADR-03 anachronism rationale; CHANGELOG (Changed #1) and ROADMAP (refactor #1
entry) likewise; the session note in the vault updated.

### M3 — `discover_notes` indexes `.claude/` agent plumbing

`_EXCLUDED_DIRS` (`src/seahorse/frontmatter/discovery.py`) covered
`.obsidian/.trash/.git/.seahorse/.svn/.hg/_darcs` but not `.claude` — plans
and session notes under a vault's `.claude/` are discovered, migrated and
indexed as user notes. Pre-existing (not introduced by v1.4.0), surfaced by
the field test on the real vault.

**Verdict: resolved.** `.claude` added to the exclusion set (a vault-level
`CLAUDE.md` file is still a note — this is a directory exclusion only). Test:
`test_run_excludes_claude_local_state_dir` (nested + top-level `.claude`
notes excluded, the real note still counted).

### M4 — unbounded/duplicated `derived_from` growth

Repeated distill/improve cycles append the prior membership each generation;
there is no cap on the emitted `x-seahorse-derived-from`, so a long
correction chain grows the field monotonically (self-edges dropped per
generation, but the lineage accumulates).

**Verdict: accepted.** Membership is O(cluster + lineage) with dedupe by id
per generation; capping it would silently discard real evidence and defeat
the field's purpose (an importer reconstructing the cluster). Parse cost is
bounded by the tolerant normalizer. Revisit only if real vaults show
pathological chains.

## Minor

### L1 — `body_hash=None` counts as a divergence

`rebuild.py`'s tracked-notes comparison (`note.body_hash != _body_hash(live)`)
fires when the additive `body_hash` field is absent (older builders construct
`ParsedNote` without it) → a spurious divergence invented from nothing.

**Verdict: resolved.** Guard `note.body_hash is not None` added; test
`test_rebuild_no_divergence_when_note_has_no_body_hash` (hashless note +
mismatching live body → no divergence).

### L2 — ROADMAP line reference off by one

`src/seahorse/cli/onboarding.py:54` — the entry point (`run_full_setup`) is
at line 55.

**Verdict: resolved** (`:55` in ROADMAP.md).

### L3 — lazy_recall docstring lacked the floor caveat

**Verdict: resolved** (see H1 — docstring gains the floor paragraph; the
CHANGELOG bullet states it too).

### L4 — the P2a refactor reverted added annotations

Several type annotations added during review were reverted by the pure-move
stage refactor.

**Verdict: accepted.** `mypy src` is clean and the runtime contract is
unchanged; re-adding them is ceremony, not safety.

### L5 — orphan membership after a later `forget`

A `derived_from` edge whose target episode is later forgotten leaves a
dangling id in the field; the consumer sees a member that no longer resolves.

**Verdict: accepted (open question).** The `x-*` preserve-never-reject policy
covers the emit side; consumers must tolerate dangling ids (they already
tolerate unknown `edge_kind`s). A forget-hook that rewrites membership of the
remaining cluster notes is a candidate for a future PR — not this release.

## Design decisions the implementation follows

- **`improve` inherits regime AND membership.** The regime marker
  (`extraction_mode='consolidated'`) keeps the note in the consolidated
  regime; the membership inheritance keeps the note's provenance complete.
  Both are regime facts about the note, not authorship facts — human
  authorship stays in `by.source_type` and `supersedes_reason=CORRECTION`.
- **One tolerant parser, two consumers.** `normalize_derived_from` serves
  both the distill supersession loop and the facade's inheritance: the same
  promotion/skip/warning policy everywhere a membership is read.
- **Rerank is time-neutral, recency/decay are not.** Only the anachronistic
  signals are PIT-gated; the cross-encoder reorder of an as-of-t set needs no
  gate.
- **Rates are floors.** The lazy-recall metric prefers under-reporting to
  invention: an unparseable response is an honest no-rows, never an
  extrapolation.
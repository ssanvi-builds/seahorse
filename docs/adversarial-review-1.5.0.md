# Adversarial review — v1.5.0 ("context persistence") before publishing

Adversarial review of the v1.5.0 sprint (`git diff 97c12ff..HEAD`: 13 files,
+452/−32, 6 commits `6707221..4b812af`) before publishing. The sprint is the
claude-mem integration plan's Phase 1: re-injection of the bootstrap on
`/clear` and auto-compaction (P1a), token economics in the bootstrap (P1b),
the `recall` description truth fix (P1d), and the release itself (P1c was
dropped per the plan's own scope rule). The attack list: the hook-contract
surface (matcher syntax, handler gating, session registration), the
upgrade path for existing installs, claims honesty (CHANGELOG vs code —
the house H1 precedent from 1.4.0), the purity/determinism contract of the
renderer, PIT disclosure claims, and release discipline (four version
fields, no stale version strings). Every claim was re-verified against the
code before it counted; refuted ones are not listed.

Each finding carries a verdict: **resolved** (fixed in this review cycle, TDD)
or **accepted** (documented trade-off). Severity as filed: 1 HIGH, 1 MEDIUM,
3 LOW — 2 resolved, 3 accepted.

## Gates re-run on the fixed tree

- Full suite: 3089 passed / 9 skipped (exit 0), coverage gate ≥80% green —
  final re-run including both review fixes.
- `ruff check src tests`: clean. `mypy src`: clean (208 files).
- e2e: harnesses 47/47 checks, fresh-user 62/62 checks — re-run on the final
  tree after both fixes.
- Determinism evidence: no retrieval-surface change in this sprint (P1b is
  render-only; P1a/P1d touch hooks and descriptions), so the 1.4.0
  fingerprint carries over; the renderer's own determinism contract is
  pinned by the golden snapshot, re-verified here after the footer fix
  (166 == len(render − economics line)//4, checked by hand and by test).

## High

### H1 — the widened matcher never reached an existing install (P1a was silently inert)

`merge_hooks` was idempotent by marker only: an entry whose command contains
the marker is not duplicated — and nothing else. An existing install kept its
pre-1.5.0 SessionStart matcher (`"startup"`) forever; re-running
`seahorse setup` was a no-op for that entry. The whole P1a feature (re-inject
on `/clear` and compaction) was inert for every user upgrading to v1.5.0,
including both of this project's machines — with the CHANGELOG claiming it
worked. Two aggravating layers, found while fixing:

- The ≤0.16.0 legacy flat entry (command at the entry level, a shape Claude
  Code ignores) was tracked for uninstall but never repaired — those
  installs never fired at all.
- `seahorse doctor` validated hook *presence* by marker only, so it reported
  `OK` on a stale install — the failure mode was invisible by design.

**Verdict: resolved.** `merge_hooks` now refreshes the matcher of
Seahorse-owned (marker-keyed) entries to the current value and restructures
legacy flat entries to the nested shape in the same pass; foreign hooks are
never rewritten. `doctor` warns on a stale Seahorse-owned matcher with the
re-run guidance ("hooks present is not hooks current"). Covered by five new
tests: matcher refresh in place (no duplication), foreign matchers never
touched, legacy flat entry repaired, doctor stale-matcher WARN (healthy →
false), and a guard that a foreign hook with any matcher stays OK.

## Medium

### M1 — the footer estimate claimed more than it measured

The Stats footer said `~N tokens to read this bootstrap` while `N` was
computed over the content blocks only — the Stats block and the pointer line
(the longest line in the render) were excluded from the very number that
claimed to cover them. ~30% understatement on the golden fixture (92 vs 166).
The 1.4.0 review's H1 was exactly this class: a claim that does not match the
code.

**Verdict: resolved.** The estimate now covers the whole render except the
economics line itself — a number cannot count its own digits, so the basis is
"everything but the sentence that states the number," which the label
tolerates within an estimate. The golden snapshot is re-pinned (166) and a
new test pins the basis (`footer == max(1, len(render − economics line)//4)`),
verified by hand before the pin. The renderer stays a pure function of
`ContextData`: identical data renders identical text.

## Minor

### L1 — the footer states the `recall_full` cost qualitatively

The plan's wording promised "estimated tokens to recall_full everything
listed." INDEX rows carry no body lengths, and purity forbids new
`ContextData` fields — any such number would be invented. The footer says
"recall_full bodies cost more — drill down selectively" instead.

**Verdict: accepted** (documented deviation at implementation time, in the
P1b commit and the CHANGELOG). Honesty about the estimate's limits beats a
fabricated number; the per-row `(~N tok)` labels give the agent the relative
cost signal it needs.

### L2 — `resume` stays outside the matcher

The matcher is `startup|clear|compact`; a resumed session does not re-inject
on SessionStart.

**Verdict: accepted** (plan binding). The plan scoped re-injection to the
context-loss moments — `/clear` and auto-compaction — because a resumed
session restores the transcript through the harness; `resume` re-injection
would double-inject a context the session already carries. claude-mem's
production `hooks.json` makes the same choice.

### L3 — P1c (raw prompt exposure in the bootstrap) was dropped

The plan's third feature (surfacing the raw user prompt in the injected
context) was not implemented.

**Verdict: accepted** (the plan's own scope rule). It would require a new
`ContextEpisode` field or body reads at INDEX level — a facade/schema change
with real friction — and the observer already captures prompts. Recorded in
the plan execution as dropped, not deferred to a promise.

## Angles that produced no findings

Recorded because they were attacked, not because they were assumed:
`register_session` is `INSERT OR IGNORE` and never resets `prompt_number`
(clear/compact re-registration is harmless); the observe handler gates on
`event_name` and never inspects `source`, so the matcher is the whole P1a
change; `PitFullNotSupported` has a single raise site (FULL level — the P1d
description "PIT is refused only in recall_full" is exact, and `skill_show`
rejects `pit` at the wire schema); the injected bootstrap is never re-captured
(`additionalContext` is not part of any hook payload the observer reads);
the pipe matcher is regex alternation, the same syntax claude-mem's
production hooks use; the version fields are exactly four and all live in
the release commit, `serverInfo.version` auto-derives via
`importlib.metadata`, and no stale version strings remain beyond the
intentional "pre-1.4.0" prose.
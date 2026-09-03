# Changelog

All notable changes to Seahorse are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **`remember` no longer advertises `tags`** — the facade rejects any
  non-empty tags in this release (`E_NOT_IN_MVP_0_1`), but the wire schema
  still advertised the field, so agents sent tags and hit an opaque
  "Primitive not available in this release". The wire now rejects unknown
  fields like `tags` up front (`-32602`), before the facade is touched.
  Re-advertise `tags` only together with facade support.
- **`remember` tool description documents the `by` provenance shape** —
  `by` must be an object with the required keys `agent_id`, `session_id`
  and `source_type`; sending a string produced an unexplained
  `Invalid params`. The description now carries a concrete example.
- **Agent instructions updated** — the block `seahorse setup` installs in
  `~/.claude/CLAUDE.md` now spells out the provenance-object shape for
  `remember`/`improve`/`forget` and warns against sending tags. Re-run
  `seahorse setup` (or `seahorse doctor --fix`) to refresh an installed
  block.

## [0.22.0] - 2026-09-03

### Added

- **Packaged agent skills** — `seahorse setup` installs a `consolidate` skill
  into `~/.claude/skills/consolidate/` so an agent session can run the
  distillation pass and enrich the notes via the seahorse-mcp tools: the
  agent's own LLM does the synthesis, so consolidating needs no API key.
  Skills carry an HTML-comment marker; a foreign `SKILL.md` is never touched
  by install or uninstall. `--no-skills` opts out; `seahorse doctor` reports
  `skills_installed` and `--fix` repairs an absent skill.
- **Paste-key provider onboarding** — the interactive remediation menu (when
  no provider passes the self-test) now offers to pull a local Ollama model,
  paste a cloud API key (Gemini, Groq, OpenRouter, OpenAI, Anthropic,
  DeepSeek — single-source catalog shared with the wizard), or skip (the
  default). The `[llm]` section is still written only after a passing live
  self-test; a failing pasted key is kept in the store and never written to
  the config.
- **Credentials store** — `~/.config/seahorse/credentials.json` maps
  env-var names to API keys: mode 0600, atomic writes, never in
  `seahorse.toml`, values never printed (masking helper). Headless commands
  (`_build_llm_client`) and `seahorse doctor` load the store automatically;
  pre-existing environment variables win. `seahorse doctor` reports the
  store (`credentials` check) and `--fix` repairs loose permissions.

### Changed

- The TTY remediation menu default is now **skip** (was: pull the Ollama
  model) — big downloads never happen on a bare Enter.
- `seahorse consolidate` with `synthesis=llm` but no wired LLM client prints
  an explicit WARN line instead of silently degrading to deterministic
  bodies.
- `setup --uninstall` also removes Seahorse agent skills (never foreign
  ones) and reports each surface once (the MCP/instructions removal block
  used to run twice).

## [0.21.0] - 2026-09-03

### Added

- `seahorse --version` — prints the installed `seahorse-memory` version and
  exits 0 (single-sourced from package metadata; eager, so it wins even when
  combined with a subcommand).

## [0.20.0] - 2026-09-03

### Added

- **One-command onboarding** — `seahorse setup` now configures the whole
  stack and always exits 0 (individual failures degrade to WARN lines):
  vault bootstrap (portable `~/seahorse-mem` default when nothing resolves
  without a TTY), eager DB creation, `[observe]`/`[materialize]` config,
  Claude Code hooks, observer start, user-scope MCP registration, the
  agent-instructions block in `~/.claude/CLAUDE.md`, and an LLM provider
  that is only written after a passing live self-test. Every step has an
  opt-out flag: `--no-mcp`, `--no-agent-instructions`, `--skip-llm`,
  `--warm-embeddings`, `--auto-consolidate`.
- **Provider bootstrap** (`seahorse.cli.provider_bootstrap`): detection in
  preference order (local Ollama qwen3-first, then cloud keys present in
  the environment), a failing primary falls through to the next candidate,
  and a big `ollama pull` is only ever offered on an interactive TTY. The
  live self-test probe moved here from `doctor` (single source of truth;
  the wizard and doctor import it).
- **`seahorse setup --auto-consolidate`** (opt-in): writes the
  `[consolidate]` section and merges a consolidate-on-stop hook. The hook
  invokes `seahorse consolidate --auto`, which no-ops (exit 0) while
  `[consolidate] auto_on_stop = false` — the Stop event never blocks a
  session, and turning the feature off is a config edit, not a hook edit.
- **`seahorse doctor --fix`**: attempts the repairs Seahorse owns for each
  actionable WARN/FAIL check (hooks, MCP, instructions, DB, consolidate);
  a failed repair is a FAIL line, never a crash. Doctor also diagnoses the
  new agent surface (`mcp_registered`, `agent_instructions`,
  `consolidate`).
- **Symmetric uninstall**: `seahorse setup --uninstall` now also removes
  the MCP registration and the agent-instructions block and stops the
  observer; the vault, its notes, `[materialize]`, and the global pointer
  stay.

### Changed

- **MCP registration is atomic and conservative**
  (`seahorse.cli.mcp_register`): direct write to `~/.claude.json` via
  tempfile + `os.replace`, a one-time `.seahorse-bak` backup, foreign
  servers preserved verbatim, a corrupt file reported and never touched,
  and a `claude mcp add` subprocess as fallback — skipped when
  `SEAHORSE_CLAUDE_JSON` redirects the config (no side-channel writes).
  `SEAHORSE_CLAUDE_MD` and `SEAHORSE_CLAUDE_JSON` override the Claude
  config paths (tests/sandboxes).
- **Agent instructions are a delimited block** in `~/.claude/CLAUDE.md`
  (`<!-- seahorse-memory:begin -->` … `<!-- seahorse-memory:end -->`):
  idempotent merge, stale blocks updated in place, user content preserved
  byte-for-byte, clean removal.
- **Non-TTY setup never hits the cold-start exit 82**: without a TTY and
  without any resolvable vault, setup bootstraps the portable per-user
  default (`~/seahorse-mem`, resolved per user at read time) so an agent
  can run onboarding unattended.
- **The SessionStart context pointer leads with the `seahorse-mcp` MCP
  tools** (the agentic use case is primary), CLI as fallback.
- The default vault option in the interactive picker is the portable
  `~/seahorse-mem` (was `~/Seahorse`).

## [0.19.0] - 2026-09-03

### Added

- **Consolidate absorbs non-human collisions** — when a distilled note
  collides with a rival untagged episode that already fed the cluster
  (source `agent`/`system`/`importer`), consolidate forgets the rival
  (`absorbed_by_consolidate`) and retries once; human-authored rivals still
  win. The report names absorbed rivals (`absorbed_rivals` in JSON,
  `[absorbed N]` in human output) instead of emitting a perpetual COLLISION
  row every run.
- **Lossless spool for hook events** — when a hook cannot deliver an
  envelope to the observer (connection failure, worker down), it writes the
  envelope to a spool directory (atomic writes, capped at 1000 files); the
  observer drains the spool into the queue at startup, so a downed observer
  no longer loses mid-session events.

### Fixed

- **`improve` preserves consolidated identity** — improving a consolidated
  note no longer breaks the semantic chain: the successor inherits
  `extraction_mode=consolidated` (no duplicate note on the next consolidate)
  and inherits the subject when the new body derives none.
- **Envelope `schema_version` is validated at the edge** — semver-shaped
  versions with known major `1` are accepted (additive evolution); unknown
  majors or malformed values fail fast with a 400 at the endpoint instead of
  being silently tolerated.
- **Doctor clarifies the orphaned `observer.lock`** — the stale-socket
  message now explains that a leftover lock file is harmless (liveness comes
  from the kernel flock, not the file).

## [0.18.0] - 2026-09-01

### Added

- **Global vault pointer + parent-directory resolution** — `resolve_vault` now
  falls back to a user-level pointer file (`~/.config/seahorse/vault` on
  Linux, `~/Library/Application Support/seahorse/vault` on macOS) and walks
  parent directories looking for `.seahorse/` (git-style). A user running
  `seahorse` from anywhere gets their vault; per-vault isolation is preserved
  because cwd/env/`--vault` still win over the pointer.
- **`seahorse setup` bootstraps the vault** — the cold-start exit 82 is gone:
  `setup` resolves the vault, then bootstraps a missing one (mkdir + minimal
  config) instead of failing. `--vault <path>` forces a directory (created if
  missing); on a TTY with nothing resolved, an interactive picker offers the
  Obsidian-registered vaults (parsed from Obsidian's own `obsidian.json`) plus
  a last option that creates a fresh vault at `~/Seahorse`. Without a TTY the
  failure stays loud (exit 82) with a `--vault` hint, so scripts never hang on
  a prompt.
- **Doctor capture end-to-end checks** — three new checks after `db`:
  `claude_hooks` (the four observer hooks as installed in Claude Code's
  settings.json, missing events named), `observer` (worker socket presence),
  and `context` (the SessionStart context rendered through the real CLI, so a
  broken injection is diagnosed instead of discovered mid-session).

## [0.17.1] - 2026-09-01

### Fixed

- **CI lint regression** (from 0.16.1) — two test fixtures exceeded the ruff
  line length, failing the lint gate and blocking the PyPI publish of
  v0.16.1/v0.17.0. No code changes; 0.17.0 never reached PyPI.

## [0.17.0] - 2026-09-01

### Added

- **Observer self-healing** — when a hook POST cannot reach the worker (socket
  absent or `OSError`), the hook respawns a dead observer. On `SessionStart`
  it waits up to 1s for the socket and retries once (advisory); mid-session
  events respawn fire-and-forget — the next hook wins. A non-200 status
  (400/401) is a live worker: no respawn. The healthy path is untouched: a
  200 POST costs zero extra work.
- **Context injection at SessionStart** — the hook now emits the bootstrap
  context as a single `hookSpecificOutput` JSON line, making the README's
  promise real: the next session starts with what previous sessions learned.
  `seahorse context` runs as a subprocess with a hard 2s timeout and degrades
  to no injection on timeout, spawn failure, non-zero exit, or empty output.
  The empty-vault bootstrap IS injected (it teaches the agent that
  `seahorse recall` exists).

### Fixed

- **Single-writer enforced by the kernel** — the observer startup now takes an
  advisory `flock` on `observer.lock`. Previously two concurrent startups
  could both pass the pid-file guard and the loser's `serve_forever` would
  silently steal the live socket; a competing run now fails loud
  (`CliObserverRunning`, exit 95) before building the facade.
- **Hook crash without `[observe]`** — `socket_path()` raised
  `AttributeError` inside the hook (only `OSError` was caught), exiting
  non-zero and surfacing as a Claude Code hook failure. The missing-config
  guard makes it a silent no-op (exit 0).

## [0.16.1] - 2026-09-01

### Fixed

- **`seahorse setup` wrote Claude Code-invalid hooks** — `merge_hooks` emitted
  `{matcher, command}` (flat command key), which Claude Code ignores with a
  settings-validation error; the shape is now the valid
  `{matcher, hooks: [{type, command}]}`. The dedup check and
  `seahorse setup --uninstall` now read commands from both the nested and the
  legacy flat shape, so re-running setup against existing hooks no longer
  duplicates entries, and the uninstall removes them all.

## [0.16.0] - 2026-09-01

### Added

- **Materialization** — episodes become visible, editable F3.1 `.md` notes in
  the vault, closing the loop between the agent's memory and the human's
  Obsidian vault. The `[materialize]` section (default `consolidated`) writes
  distilled knowledge (`extraction_mode=consolidated`) and project notes
  (`cognitive_type=project_doc`) to `Memory/`; `all` writes every currently-valid
  episode; `off` disables it. `seahorse setup` writes the section; `seahorse
  materialize` backfills. The write path, `distill`, `improve`, and `forget`
  all materialize/invalidate through one facade injection point (best-effort —
  a materializer failure never fails the write).
- **Editorial distillation** — the agent writes project notes via MCP `remember`
  with `cognitive_type=project_doc`; Seahorse indexes and materializes them, and
  `recall(query, cognitive_type="project_doc")` finds them again. The pattern is
  documented in `docs/editorial-notes.md`.
- **Human-edit guard (id-based, C3)** — the materializer compares frontmatter
  ids, not mtimes: a same-slug note that is not ours gets a `{slug}-{id8}.md`
  suffix and is never overwritten; `forget`/`improve`/supersession invalidate a
  note by merging `invalid_at` into the frontmatter, preserving the current body
  (a human edit survives). The consolidate editorial-authority guard now skips
  seahorse's own materialized notes the same way.

### Fixed

- **`skip_extraction` unification (M2)** — the hot write path now derives
  `skip_extraction` from `extraction_mode` (1 for `skip`, 0 otherwise) instead
  of hardcoding 0, matching the rebuild. The divergence was visible once the
  rebuild re-parsed seahorse's own materialized `.md`.

## [0.15.0] - 2026-08-31

### Added

- **Pre-v1.0 validation harness** — the published artifact is now verified
  from PyPI on a clean machine before v1.0. `scripts/e2e-pypi.sh` installs
  `seahorse-memory[embeddings,llm]` from a configurable index into a fresh venv
  and runs the README onboarding plus MCP over all three launch paths (binary,
  `seahorse mcp` subcommand, `uvx`). `scripts/e2e-vm.sh` provisions a clean
  Linux VM (OrbStack debian:bookworm, no uv/git/build-essential) and runs the
  full onboarding with real embeddings (fresh mE5-small download) and real LLM
  extraction (Ollama in-VM, custom `qwen3:0.6b-t2` model). `scripts/e2e-loop.sh`
  validates the self-evolving loop end-to-end (observe → consolidate/supersede
  → recall → decay). The publish workflow now gates PyPI on a TestPyPI
  verification chain (`publish-testpypi` → `verify-testpypi` →
  `build-and-publish` → `verify-pypi`).

### Fixed

- **Doctor provider self-test tolerates small local models** — the probe schema
  requires only `subject` (extra fields allowed), so small models that emit
  `valid_at` from the extraction pattern no longer fail the self-test.

## [0.14.0] - 2026-08-27

### Added

- **Two-stage session→episode experiment** — `two_stage_retrieval` benchmark
  experiment measuring the oracle upper bound of a two-stage design (session →
  episode with PERFECT golden-session identification): the golden session's
  episodes are re-ranked by a real hybrid score (vector + BM25 over bodies),
  and `two_stage_episode_recall_{1,3,5}` = `session_recall × within_session_top_m`
  is the decision headline. New `experiments/two_stage_retrieval.py` (frozen
  `TwoStageExperimentResult`; pure `_hybrid_rank_within_session`; R3-prior
  decay-free `decide_two_stage` with the explicit 5pp threshold —
  `invalid_regime` on `fallback_g2`, `two_stage_indicated` iff
  `two_stage@5 >= episode_recall@k + 0.05`, else `two_stage_not_indicated`),
  wired into `EXPERIMENTS`, the runner dispatch/render and the CLI. Synthetic
  corpus verifies the mechanics (3 cases, 9 queries, HashEmbedder):
  session_recall 6/9, two_stage@5 6/9 → `two_stage_indicated`.
- **Authoritative decision (2026-08-27)** — the oracle flips
  `two_stage_indicated` (two_stage@5 **0.707** ≥ 0.533 + 0.05), so the
  conditional fix was implemented AND measured: `session_id` denormalized into
  `episode_index` (migration 012), and a `session_boost` seam in `recall` that
  identifies the majority session from the fused top-k, re-ranks its episodes
  by hybrid and appends the fresh ones. The automatic version is **NET-HARMFUL**
  on LMEB-S in every design (aggressive 0.326, merge 0.424, append-only 0.424
  vs pure-RRF **0.533**; session hits 79 → 62): sessions are short (~4 turns)
  and the top-10 surfaces 1–2 episodes per session, so majority identification
  ties and degenerates — session identification is the bottleneck, not the
  re-rank. The oracle upper bound is NOT engine-realizable. `session_boost`
  ships **disabled by default** (SUT byte-identical to v0.13.0); the seam,
  migration and tests stay as measured infrastructure. See `docs/benchmark.md`.

## [0.13.0] - 2026-08-27

### Added

- **Context-assembly experiment** — `context_assembly` benchmark experiment
  that decomposes the answer-in-context gap (episode recall 0.533 →
  answer-in-context 0.350) into disjoint per-query buckets: `context_hit` /
  `hydration_failure` / `retrieval_miss` / `single_token` / `unlocalized`
  (invariant: they sum to `n_queries`). New `experiments/context_assembly.py`
  (frozen `ContextAssemblyExperimentResult`; pure `_classify_query`;
  `decide_context_assembly` with explicit thresholds in precedence order —
  `invalid_regime` on `fallback_g2` (fail-loud), `hydration_bottleneck`
  (flip=True), `retrieval_ceiling`, `metric_ceiling`, `context_assembly_ok`;
  the flip is True ONLY for the defective-assembler case), wired into
  `EXPERIMENTS`, the runner dispatch/render and the CLI. The reported
  `answer_in_context_summary` diagnostic does NOT decide (representation is
  already closed by `reader_context`). Synthetic corpus verifies the mechanics
  (3 cases, 9 queries, HashEmbedder): case A context_hit, case B
  retrieval_miss, case C single_token.
- **Authoritative decision (2026-08-27)** — `context_assembly` on the
  reproducible 100 subsample (retrieval-only): episode recall 0.533,
  answer-in-context 0.340, and **`metric_ceiling`** — `metric_ceiling_rate
  = 54/100 = 0.54 >= 0.15`, dominated by 46 single-token + 8 unlocalized
  answers that can never be hits with `min_ngram=2`. **No fix.** The
  conditional fix (`batch_body_for` hydration robustness) is NOT indicated:
  `hydration_failures = 0`, assembly efficiency 1.0 — the assembler works.
  Two-stage session→episode stays a documented follow-up candidate for the 15
  retrieval misses, not a flip. This closes the A4 chain explanation: the
  answer-in-context gap is largely a metric artifact. See
  `docs/benchmark.md`.

## [0.12.0] - 2026-08-27

### Added

- **Reader-quality A/B experiment** — `reader_quality` benchmark experiment
  that measures end-to-end accuracy with a WEAK reader (the A4 baseline
  `qwen3:0.6b`) vs a STRONG reader (the cloud model
  `deepseek-v4-flash:0731-cloud`) over ONE corpus — the evidence for whether
  the reader MODEL is the bottleneck (the last A4 suspect after representation,
  granularity and ranking were ruled out). New `experiments/reader_quality.py`
  (corpus built once, both readers measure over the same facade;
  `decide_reader_quality` with the 10pp `READER_QUALITY_DELTA_PP` flip
  threshold; `invalid_regime` on `fallback_g2`), wired into `EXPERIMENTS`, the
  runner dispatch/render (real readers for `lmeb-s`, deterministic doubles for
  synthetic CI) and the CLI `--strong-reader-model` flag. Synthetic corpus
  verifies the mechanics (abstaining weak reader 0.0 → extractive strong 0.5).
- **Authoritative decision (2026-08-27)** — `reader_quality` on the
  reproducible 100 subsample (real readers, active-now): weak 0.060, strong
  **0.040**, delta **−2.0pp** on recall@10 0.790 — the strong reader recovers
  nothing. **`context_assembly_bottleneck`** — falsifies the A4 reader-quality
  hypothesis; the reader model is NOT the bottleneck. This closes the A4 chain
  (representation, granularity, reader model all ruled out); the remaining loss
  is context assembly (answer-in-context 0.350). See `docs/benchmark.md`.

## [0.11.1] - 2026-08-25

### Added

- **Episode-granularity experiment** — `episode_granularity` benchmark
  experiment that measures whether the ANSWER-BEARING episode (not just its
  golden session) reaches the top-k — the remaining A4 suspect after ranking
  (session recall@10 0.790) and reader-context (body +2.0pp) were ruled out.
  New pure `experiments/episode_locator.py` (longest contiguous n-gram of the
  answer within episode bodies; verbatim/fragment/single-token/unlocalized
  statuses; injectable embedding fallback) and `experiments/episode_granularity.py`
  (session vs episode recall@10, within-session top-1/3/5 via vector-only
  re-ranking, answer-in-context rate, `decide_episode_granularity` with the
  reader-bottleneck / two-stage / not-retrievable thresholds, `invalid_regime`
  on `fallback_g2`), wired into `EXPERIMENTS`, the runner dispatch/render and
  the CLI help. Synthetic CI corpus verifies the mechanics (case A:
  recoverable episode → 1.0/1.0; case B: session-only → 1.0/0.0).
- **Authoritative decision (2026-08-25)** — `episode_granularity` on the
  reproducible 100 subsample (retrieval-only, active-now): episode-level
  recall@10 **0.533** clears the 0.5 gate — the answer-bearing episode IS
  retrieved in a majority of queries, so granularity is not the dominant loss.
  Within-session top-1/3/5 = 0.413/0.685/0.826, answer-in-context only 0.350.
  **`reader_bottleneck`** — falsifies the A4 granularity hypothesis; follow-up
  is reader quality / context assembly, NOT two-stage retrieval. See
  `docs/benchmark.md`.

## [0.11.0] - 2026-08-22

### Added

- **Reader-context A/B experiment** — `reader_context` benchmark experiment
  (summary | body | body_bounded) that falsifies the A4 `reader_bottleneck`
  hypothesis: does hydrating the FULL body close the end-to-end gap? New
  `harness/context.py` seam (`ContextMode`, pure `assemble_context`,
  `batch_body_for` wiring over `recall_full` in batches ≤ `MAX_FULL_BATCH`),
  `experiments/reader_context.py` (measures e2e accuracy across the three modes
  over the SAME corpus, `decide_reader_context` flips iff a body mode recovers
  ≥ 10pp over summary, `invalid_regime` on `fallback_g2`), wired into
  `EXPERIMENTS`, the runner dispatch/render and the CLI (`--context-mode`,
  `--reader-model`).
- **H2 fix — the reader now sees the question** — `ReaderLLMClient.generate`
  received `question` but discarded it (built `messages=[system, context]`); the
  user message now includes `"Question: {question}\n\nRetrieved context:
  {context}"`. Any end-to-end measurement with the real reader was null until
  this fix.
- **Authoritative decision (2026-08-22)** — the reader-context A/B on the
  reproducible 100 subsample with the real reader (`ollama/qwen3:0.6b`, t=0,
  seed=42): recall@10 0.790 (ceiling) · e2e summary 0.070 · body 0.090 ·
  body_bounded 0.090 → **keep_summary** (delta 2.0pp < 10pp). The summary
  representation is NOT the reader bottleneck; the ~70pp recall→e2e gap points
  to episode-level retrieval granularity or reader quality (follow-ups). See
  `docs/benchmark.md`.

## [0.10.0] - 2026-08-21

### Added

- **Reproducible balanced LMEB-S subsample harness utility** — `subsample.py`
  materializes the documented compromise as a deterministic utility: the
  balanced 100-question subsample of `longmemeval-s-s` (seed 42, composition
  40 temporal-reasoning / 30 knowledge-update / 20 multi-session / 10
  single-session-user), with the split hash **recomputed over the subsampled
  instances** so the fingerprint identifies the subsample honestly
  (`c6178fd0a436`). Fail-loud when a per-type quota cannot be satisfied.
- **Authoritative LMEB-S standalone runs** — `build_real_corpus` for
  `rrf_k` (A5), `rerank_body` (A6) and `end_to_end` (A4): ingest the real
  haystack over the subsample (45,580 episodes) with the real fastembed
  embedder, and measure **session-level recall** (any retrieved episode from
  the golden session) via the `ep_id_to_session` bridge — LMEB answers live in
  sessions, not a single turn. Shared `lmeb_corpus.py` builder +
  `--subsample/--no-subsample` CLI flag wired through the runner.
- **Authoritative decisions (2026-08-21)** — the five milestone experiments
  run on the reproducible subsample (retrieval-only, ADR-10): `rrf_k`
  **keep_60** (recall@10 0.790 flat across the sweep), `rerank_body`
  **reopen_rerank** (body 0.830 vs summary 0.660 — the summary representation
  was the F2 culprit), `end_to_end` **reader_bottleneck** (recall 0.790 / e2e
  0.070), `decay_rrf` **keep_off** and `recency` **keep_off** (both seams
  default-off confirmed: no delta on the real corpus). See `docs/benchmark.md`.

## [0.9.0] - 2026-08-20

### Added

- **Decay ranking bias (Sprint D, opt-in, default-off)** — a FAMA-style
  Ebbinghaus forgetting curve in the retrieval engine:
  `score' = score · 2^(-age_days/half_life[type])`, factor in `(0, 1]`. The bias
  folds INTO `FusedCandidate.score` post-RRF (never an external reorder),
  downweighting stale knowledge by `created_at` age with S₀ half-life priors per
  `cognitive_type` (episodic 139d, semantic 347d, social 231d, procedural 347d;
  unknown/missing types fall back to the conservative 347d default — R3
  experiment priors, NOT grounding). Reads `created_at` + `cognitive_type` in
  batch via `index_repo.get_rows` (one IN query, no N+1). Honest degradation: a
  candidate with no index `created_at` is left undecayed; a failure in the
  optional signal keeps the current ranking (BLE001 + warning), never killing
  it.
- **Composition-root swap** — `build_facade(..., decay=DecayConfig | None)` and
  `recall(..., decay=...)`; default `None` keeps the pure-RRF bit-comparable
  fingerprint. `HybridRetriever` passes it through. PIT queries reproduce state
  as-of-`t` with pure RRF — the bias never crosses axes (ADR-03). No writes
  (R2): the read path never writes; `expired_at` stays NULL. No `x-*`
  provenance reads in the core (R1). Can coexist with the F1 recency boost
  (recency folds first, then decay — multiplicative compound, deterministic).
- **Benchmark `mvp1_decay` variant + `decay_rrf` experiment** — baseline
  `mvp1_rrf` (decay OFF) vs `mvp1_decay` (ON with the R3 priors), runnable via
  `seahorse benchmark experiment decay_rrf` and the `--decay-half-life` flag on
  `seahorse benchmark run`. `decide_decay_rrf` flips decay operational iff
  recall@10 improves ≥ 1pp on the knowledge-update slice without degrading
  global ndcg@10 by > 1pp; otherwise stays default-OFF. The standalone F7(g)
  `experiment decay` (FAMA/MPA measurement) is unchanged.

## [0.8.0] - 2026-08-19

### Added

- **LLM synthesis for distillation (`seahorse consolidate --synthesis llm`)** —
  the off-path F7+ block: 1 LLM call per recurrent cluster (N episodes → 1
  fact), reusing the extractor seam (schema hint + repair + honest degrade).
  A failed synthesis degrades to the deterministic fallback with a durable
  `degraded_from="llm"` marker (ADR-10). Opt-in via the `[distill]` config
  section (`synthesis = "llm"`) or the CLI flag; the deterministic distillation
  remains the default.
- **`LLMClient.extract` `prompt_builder` seam** — an optional custom prompt
  builder lets callers reuse the full extraction pipeline (schema validation,
  repair loop, fallback chain, cost cap) with a different prompt. Additive and
  non-breaking.
- **Distillation supersession (`seahorse consolidate --supersede`)** — a cluster
  whose key already has a consolidated note gains NEW valid episodes → the note
  is UPDATED via `improve` (invalidate + atomic append) instead of duplicating.
  Editorial authority: a note whose vault `.md` was edited after its creation is
  human-touched and never superseded (the human prevails). Opt-in via the
  `[distill]` config section (`supersede = true`) or the CLI flag; the
  idempotent skip remains the default.
- **BM25 OR-of-terms query** — the FTS5 query is now built from the question's
  tokens (OR-of-terms) instead of phrase-quoting the whole query, so a
  natural-language question matches episodes containing any of its terms (the
  hybrid RRF was effectively kNN-only before).
- **Distillation indexes the consolidated note** — `facade.distill` now fires
  the write-path index hook, so the consolidated knowledge note is recoverable
  by hybrid recall (it was invisible to vec0/FTS before).
- **`[observe].drop_tools` applied at enqueue** — a tool added to the configured
  drop set never reaches the observer queue (previously it was only dropped at
  drain, so its redacted content persisted on disk).

## [0.7.0] - 2026-08-16

### Added

- **MCP `skill_list` / `skill_search` tools** — the MCP server now exposes the
  two Discovery-level skill tools (14 tools total), closing the parity gap with
  the CLI (which had `skill add|list|search|show` but no MCP listing/search).
- **CLI `freshness-view` / `audit-log` / `follow-supersedes-chain`** — the three
  read-only facade tools are now available on the terminal (parity with the MCP
  server, which already had them).
- **Timeline axes `created_at` / `valid_at`** — `recall-timeline` can now range
  over the transaction-time (`created_at`) and valid-time (`valid_at`) axes
  around an anchor's timestamp (±7 days), in addition to the supersedes chain
  and fact-id scope.
- **`--verbose` / `-v` flag** — per-operation timing on stderr
  (`[verbose] <label> took <X>ms`) for the memory primitives and skill commands.
- **BFS node budget** — the graph traversal is bounded to 1000 visited nodes,
  keeping exploration fast on large graphs (deterministic truncation).

## [0.6.0] - 2026-08-16

### Changed

- **Distribution renamed to `seahorse-memory`** — the PyPI name `seahorse` is
  taken by an unrelated project, so the distribution is now published as
  `seahorse-memory` (`pip install seahorse-memory`). The import package, the
  `seahorse` / `seahorse-mcp` console scripts, and the repo name are unchanged.
- **README rewritten for onboarding** — the README now leads with the problem
  and a Claude Code use case, adds a comparison with other memory tools, a
  curated benchmark section, and an FAQ, and moves the release-status block to
  the end. Internal jargon (`CLI_NOT_IN_MVP_0`, reserved Spanish command names)
  was removed from the public docs.
- **Benchmark published** — `docs/benchmark.md` documents the LMEB-S harness,
  the current numbers, their caveats, and how to reproduce them.
- **`scripts/demo.sh`** — a presentational walkthrough (init → remember →
  recall → improve → forget → import → observe status) for demos and recordings.

### Added

- **`seahorse frontmatter migrate`** — CLI command to migrate a legacy Obsidian
  vault (notes with no frontmatter, or legacy `tags`/`created` fields) to the
  canonical episode format. `--dry-run` classifies every note and writes nothing
  (always exit 0); `--resume` skips notes unchanged since the last manifest;
  `--batch-size` sets the manifest checkpoint cadence. Works before `seahorse init`.
- **Exit `97` `CLI_MIGRATION_DEFERRED`** — when apply meets incompatible notes
  (case D), the manifest summary is printed first, then the command fails loudly
  so scripts can see the vault is not fully migrated.
- **`publish.yml` workflow** — release-on-tag build + publish to PyPI as
  `seahorse-memory`.

### Fixed

- **Actionable `FrontmatterInvalid` error** — the message now names the migration
  command (`seahorse frontmatter migrate`) for legacy Obsidian notes, instead of
  pointing at the schema DDL runner.

## [0.5.1] - 2026-08-12

### Added

- **`scripts/e2e-matrix.sh`** — fresh-user end-to-end testing across environment
  combinations (install method × extras × Obsidian × Ollama × online/offline ×
  vault state × concurrency). 8 priority combos, isolated sandbox per combo,
  PASS/FAIL report. `--ci-subset` runs the CI-safe combos (`core_min` +
  `uv_sync_dev`); `--list` shows all combos.
- **`scripts/stress-core.sh`** — core load-test: ingest 1000+ episodes, recall
  `--top-k 100` p95 ≤ 250ms, concurrent single-writer, reindex, idempotent
  import, improve/forget chain.
- **CI job `e2e-matrix`** — runs the CI-safe matrix subset on every push/PR.

### Fixed

- **`scripts/e2e-fresh-user.sh`** — the MCP `tools/list` assertion now checks the
  7 memory primitives as a superset instead of an exact count of 7 (the surface
  grew to 12 tools).
- **Actionable `enable_load_extension` error** — a Python build without
  `SQLITE_ENABLE_LOAD_EXTENSION` (e.g. a pyenv build) used to crash every DB
  command with a cryptic `AttributeError`; it now fails with a hint to install
  with a supporting Python (`uv tool install --python 3.13`).
- **Actionable `E_FRONTMATTER_INVALID`** — `index rebuild` on a legacy Obsidian
  note surfaced only raw pydantic validation errors; the message now says the
  note is not in the canonical format and names the required fields.

### Docs

- README: "7 memory-native primitives" → 7 primitives + 5 procedural/read-only
  tools (12 total); new Testing section (matrix + stress).

## [0.5.0] - 2026-08-10

### Added

- **Procedural skills** — `seahorse/procedural/`: `record_procedure`
  (deterministic creation, near-zero cost, canonical body
  `## Trigger/Steps/Validation/Rationale` validated before any write) ·
  `ProceduralShaper` (3-level progressive disclosure: Discovery = INDEX summary
  ≤280, Activation = TIMELINE, Execution = FULL) · trust gate (manual high /
  agent medium / import+distilled low; low-trust skills are delivered as
  citation/context, not instruction) · CLI `seahorse skill add|list|search|show`
  (add validates the canonical body, show applies the trust gate).
  `ProceduralError` maps to CLI exit 96 / MCP -32053.
- **Graph (BFS) timeline axis** — `materialize_timeline(axis=graph_bfs)`:
  a 1-2 hop, point-in-time-aware traversal of the supersedes graph,
  `HopsCapExceeded` for hops > 2, `cognitive_type=semantic` filter. Exposed via
  `recall-timeline --axis graph_bfs --hops` and the MCP `recall_timeline` `hops`.
- **Viewer TUI** — `seahorse view`: read-only interactive stdlib TUI
  (recent / search / timeline / skills), with an honest empty-vault degrade.
- **`[procedural]` config** — `seahorse.toml` section (min_trust + loadout
  defaults; opt-in, missing → module defaults).

## [0.4.0] - 2026-08-10

### Added

- **Session observer** — `seahorse/observe/` (stdlib-only): capture of agent
  sessions — a tolerant event envelope with size caps, deterministic redaction
  of sensitive values (bearer/API keys, PEM, userinfo, URL prefixes), tool
  thresholding (skip WebSearch/WebFetch, drop Read/Bash), deterministic turn
  batching (byte-truncation never splitting a codepoint), a dedicated SQLite
  queue (single-writer WAL + ack, dedup by session/turn/fingerprint), a worker
  that drains by session (skip-first, deterministic summary), a unix-socket
  endpoint (0600 + auth token, redacts before enqueue, dropped tools never
  persisted), and a Claude Code adapter (4 hooks: SessionStart / UserPromptSubmit
  / PostToolUse / Stop). CLI `seahorse observe start|stop|status|run|event`.
- **Context bootstrap** — `MemoryFacade.context()` (four INDEX-level blocks:
  recent, current-state, last session grouped by session — a list, not an
  abstractive summary) + `seahorse/context/assembler.py` (pure render) + CLI
  `seahorse context` (degrades to "no context" without a DB).
- **Consolidation** — `seahorse/distill/`: `cluster.py` (clustering key distinct
  from the stored subject, N≥3) · `distill.py` (writes a semantic episode with
  `cognitive_type=semantic` that supersedes the representative source; sources
  stay current) · `consolidate.py` (idempotent) + additive
  `supersedes`/`supersedes_reason` support on the write path + CLI `seahorse
  consolidate`.
- **Setup** — `seahorse setup` (writes `[observe]` with a generated auth token +
  merges the Claude Code hooks into `~/.claude/settings.json`, coexisting with
  claude-mem) + `--uninstall`.
- `[observe]` section in `seahorse.toml` (opt-in until `seahorse setup`).
- **Cross-encoder reranking stage (inactive opt-in)** — a `QueryReranker`
  contract, a stage-3 rerank step in `recall()`, and a FastEmbed cross-encoder
  backend. The default remains pure RRF fusion; an early evaluation on the
  benchmark corpus showed the cross-encoder degraded retrieval quality and
  exceeded the latency budget, so it stays disabled pending re-evaluation.
- **`--summary` on import with turn-structure preservation** — `seahorse import`
  preserves the source tool's session/turn identifiers in episode provenance
  (`x-claude-mem-session-id` / `x-claude-mem-prompt-number`), so the session
  structure survives the migration.
- **Embed-mode option** — `RetrievalIndexer(embed_mode="body" | "body+summary")`
  and `--embed-mode` on the benchmark CLI and `seahorse index rebuild`.
  `body+summary` embeds the summary followed by the body; reindexing under a new
  mode re-embeds honestly (the effective text changes).

### Fixed

- **`seahorse setup` crashed on a fresh user** (no `~/.claude/`): `merge_hooks`
  now creates the parent directory before writing `settings.json`. The observer
  is a Claude Code capture adapter, not a product binding — the rest of Seahorse
  is agent-agnostic.
- **Observer spawn**: `observe start` placed `--vault` after `observe run`, but
  it is a global option that must precede the subcommand — the observer died
  immediately with "No such option: --vault". Fixed in `observe/cli.py` + an
  order assertion in the test.
- **`seahorse doctor` prerequisite checks**: new `python` / `uv` / `obsidian` /
  `sqlite_vec` checks (the last detects a Python build whose `sqlite3` lacks
  `enable_load_extension`, which breaks sqlite-vec with a cryptic
  `AttributeError`).
- **README Prerequisites section** (Python ≥3.11 + sqlite load_extension, uv,
  Obsidian optional).
- **`scripts/e2e-fresh-user.sh`** — fresh-user E2E in an isolated sandbox
  (overridden HOME, temp vault; never touches the real `~/.claude` /
  `~/.claude-mem`). Validates install → init → core CLI → hybrid embeddings
  → LLM (honest degrade) → observer → import → MCP, with no-corruption
  post-flight checks.

### Changed

- **Embed mode default `body+summary`** — retrieval embeddings now lead with the
  summary (`summary\n\nbody`). This is the new default in the facade, the
  benchmark config, `seahorse index rebuild`, and the indexer; it measurably
  improved recall on the benchmark corpus. The `body`-only mode remains
  available for comparison.

## [0.3.0] - 2026-08-07

### Added

- **Recency as an opt-in ranking signal** — `apply_recency_boost`, a small
  bounded exponential-decay step applied after RRF fusion in `recall()`
  (`score' = score · (1 + γ·exp(-ln2·age_days/half_life))`, factor in
  `[1, 1+γ]`). Gated on point-in-time queries being off (PIT reproduces state
  as-of `t` with pure RRF); **default-off** (`recency=None`) preserves the
  bit-comparable fingerprint. `created_at` is batch-read (one `IN` query, no
  N+1). CLI flags `--recency-gamma` / `--recency-half-life`.
- **`remember --summary`** — `summary` is an optional editorial field on
  `RememberPayload` (facade + CLI `--summary` + MCP wire). When absent, the write
  path derives a deterministic zero-LLM fallback (first sentence of the body,
  skipping the H1, truncated to 200 chars) — covering 100% of episodes including
  the skip path. The write path persists it; the frontmatter round-trip
  preserves it.
- **claude-mem importer** — `seahorse import [--source] [--mode dry-run|commit]
  [--project]`: migrates claude-mem observations to episodes with a loss report,
  a manifest (`seahorse.importer.manifest/1.0`), idempotency via deterministic
  UUIDv5, and collision detection (never raised — reported). claude-mem is never
  a runtime dependency.

## [0.2.0] - 2026-08-06

Hybrid semantic retrieval (sqlite-vec kNN + FTS5 BM25 fused by Reciprocal Rank
Fusion) with point-in-time routing, a FastEmbed (ONNX, mE5-small) embedder, a
real multi-LLM extraction path with a local-first CI gate, and an honest degrade
to a current-state listing when vectors/embedder are unavailable.

### Added

- Migration 010: the `vec0` virtual table (`vec_episodes`, float[384]) + the
  FTS5 external-content pair (`episode_content` / `episode_fts`).
  `schema_version = 10`.
- `SqliteVectorIndexRepository` (kNN with current-state/fact_id/cognitive
  pushdown, point-in-time `state_at`/`known_at` predicates) and
  `SqliteFullTextIndexRepository` (BM25, `exp(-bm25)` scoring, PIT, subject
  filter) — real backends over the migration-010 tables.
- `seahorse/embeddings/`: `ModelIdentity` + async `Embedder` Protocol + L2
  normalization; the FastEmbed ONNX backend (mE5-small fp32-O4 bundle); a sync
  `QueryEmbedder` adapter; a query cache (SQLite + LRU); and a `RetrievalIndexer`
  that populates vec0/FTS from the write path and `seahorse index rebuild`
  (best-effort).
- `HybridRetriever`: the facade recall regime over `seahorse.retrieval.recall`
  (RRF fusion, point-in-time routing), with an honest degrade to the
  current-state listing when vectors/embedder are unavailable.
- Point-in-time recall support in the hybrid regime (the facade guard is now
  conditioned on the retriever's `supports_pit` capability).
- New optional `embeddings` extra (`fastembed`, `onnxruntime`) — the default
  install stays offline (no model download).
- `seahorse/llm/`: errors taxonomy (retry/content/permanent), providers registry
  (ollama/gemini/groq/openrouter/openai/anthropic/deepseek/vllm — local-first),
  extraction role routing, operative cost cap (local and free-tier models price
  at $0), plain-prompt parser + Pydantic validator (`extra="forbid"` → repair
  loop, `<content>` injection delimiters), retry/fallback chain (backoff +
  jitter), and the `LiteLLMBackend` (optional `llm` extra).
- `run_llm_path` (write path) with a strict episode frontmatter (subject
  required); the write path gains an additive `subject` override;
  `build_facade` gains the `llm_client` slot.
- CLI onboarding: `seahorse init --llm` no-TUI provider wizard (detects Ollama /
  free-tier keys; factory default local-first `ollama/qwen3:1.7b`, 0.6b low-end);
  `[llm]` block in `seahorse.toml`; `status` reports the LLM regime; `seahorse
  doctor` (config + key names + live provider probe).
- CI gate `ci-llm-gate.yml`: the real extraction path is run against the weakest
  model of the family (`ollama/qwen3:0.6b`, pinned Ollama image, CPU) so the
  validator + retry + repair must carry the load — proving the path does not
  silently depend on native structured outputs or on a strong model. Gated tests
  in `tests/llm/test_gate_ollama.py` (enabled via `SEAHORSE_RUN_LLM_TESTS=1`,
  `pytest -m llm_gate`); the main `ci.yml` is untouched (still no litellm).
- **Frontmatter round-trip: `extraction_mode=consolidated`** is now schema-valid
  and round-trippable (wire + facade + frontmatter, case-C idempotent): a
  batch-distilled note with `extraction_mode=consolidated` parses, round-trips
  byte-identically, and is left untouched on re-run. The wire enum is
  single-sourced from the facade `ExtractionMode` Literal. `llm_partial` stays
  fully reserved.

### Fixed

- The extraction prompt now states two rules verbatim for weak models: `subject`
  is a short topic phrase — never a bare date; `valid_at` must be a
  timezone-aware ISO-8601 datetime, so a bare date is omitted rather than
  emitted. A weak model previously used a bare date as both `valid_at` and
  `subject`, wasting repair calls.

### Known Limitations

- The mE5-small bundle is fp32-O4 (~235MB): no int8/fp16 artifact is portable to
  Apple Silicon; a portable int8 bundle is a measured follow-up (Optimum
  quantization + per-platform benchmark).
- Without the `embeddings` extra (or with no vectors populated), `recall` is the
  honest current-state listing (no ranking) and point-in-time recall is refused.
- The graph-expansion retrieval axis (BFS into the fusion) is a medium-term
  goal; the supersedes chain is already fused.
- **Batch distillation is NOT built yet**: `consolidated` is a valid,
  round-trippable schema value, but the engine does not produce it — the
  single-episode write path refuses it loudly.
- Reserved CLI commands (`expire`, `revalidate`, `vigentes`, `activos-ahora`,
  `index verify`) still exit `75`.

## [0.1.0] - 2026-07-29

First tagged release. The memory engine records, recalls, improves, and forgets
episodes end-to-end from a clean install, and serves an agent over stdio MCP.

### Added

- Bi-temporal, append-only episode store on stdlib `sqlite3` (single-file,
  zero-infra). Auto-migrating schema (`schema_version = 9`).
- The 7 memory-native primitives on both the CLI and stdio MCP
  (`io.seahorse.memory/v1`, protocol pinned `2025-11-25`):
  `remember`, `recall`, `recall_timeline`, `recall_full`, `improve`, `forget`,
  `build_pit`.
- Progressive disclosure across three retrieval levels — INDEX (current-state
  listing), TIMELINE (supersedes chain), FULL (hydrated episode) — plus
  point-in-time projection via `build_pit`.
- Supersession (`improve`) and soft-delete (`forget`), append-only; full history
  is preserved and reproducible at any past point in time.
- Two console scripts: `seahorse` (humans/scripts) and `seahorse-mcp` (agents);
  the `seahorse mcp` subcommand delegates to the same stdio server as
  `seahorse-mcp`. `serverInfo.version` is single-sourced from package metadata.
- Frontmatter import/export for the Obsidian vault layer (markdown as the
  portable on-disk contract).
- Honest exit codes with a structured `{"error": {...}}` envelope on stderr
  (`seahorse_code` / `cli_code` / `exception_class`) so agents and scripts branch
  deterministically.
- Systematic functional review committed as regression tests: subprocess CLI
  smoke (full first-release matrix), real-stdio MCP smoke, and a gated install
  smoke proving the "clone, install, run" promise.

### Known Limitations

- `recall` returns the **current-state listing** clamped to `top_k`; the query is
  validated non-empty but does **not** filter or rank in v0.1.0. This is
  deliberate and documented, not a gap.
- No embeddings, vector search, or FTS5 retrieval yet — that landed in v0.2.0.
- No LLM extraction yet — the skip-path is first-class so an agent records at
  near-zero cost today.
- Reserved CLI commands (`expire`, `revalidate`, `vigentes`, `activos-ahora`,
  `index verify`) return exit `75` with a reason, rather than silently
  no-op'ing.
- The FastAPI / SQLAlchemy / LiteLLM / multilingual-e5 / ONNX stack from the
  long-term design is **not** in v0.1.0; it lands in later releases and the
  multi-agent tier (Postgres + pgvector).

# Seahorse benchmark

Seahorse ships a reproducible benchmark harness for memory retrieval. This
document states the methodology, the current numbers, their caveats, and how to
reproduce them.

The point is not a leaderboard. It is an honest, reproducible measurement — the
field's own benchmarks are hard to trust (LOCOMO has 6.4% wrong gold answers;
Mem0's reproduction is broken, issue
[#2800](https://github.com/mem0ai/mem0/issues/2800)), so Seahorse publishes its
harness and its numbers together, with the caveats spelled out.

## Methodology

- **Dataset**: `longmemeval-s-s` v1.0.0 (a subsample of the LongMemEval
  benchmark), split `38f851629178`. The run uses **n≈470–500 questions**, not
  the full dataset.
- **Retrieval**: hybrid semantic retrieval — sqlite-vec kNN + FTS5 BM25 fused
  with Reciprocal Rank Fusion (`score_source: mvp1_rrf`), top-k 10.
- **Judge**: relevance is scored by a small LLM (`ollama/qwen2.5:7b`), with
  `judge validation: unvalidated_with_small_model` — the judge has **not** been
  validated against human labels.
- **Reader**: `ollama/qwen3:1.7b` (t=0, seed=42).
- **Reproducibility**: `local_near_deterministic` (0.956 expected match) —
  reproducible locally, not bit-exact across machines.

## Current numbers

Run `4ec750cd9adb34fa`, `score_source: mvp1_rrf`.

| Metric | Value | Note |
|---|---|---|
| recall@10 | 0.1296 | knowledge-update slice: 0.4375 |
| ndcg@10 | 0.1105 | |
| mrr | 0.1268 | knowledge-update slice: 0.4724 |
| precision@10 | 0.0235 | |
| token efficiency | 0.9976 | 51.5M tokens full-context → 121K measured |
| latency p95 (INDEX) | 42.08 ms | retrieval-only, no rerank |
| fama_gap | 0.0 | sanity check, no baseline by design |
| knowledge_update_accuracy | 0.0 | not measured in retrieval-only mode |

The knowledge-update slice is the strongest (recall@10 0.44, mrr 0.47); the
temporal-reasoning slice is the weakest (recall@10 0.02). This is expected for a
retrieval-only pass — the engine ranks by relevance, it does not reason.

## Authoritative experiment decisions — 2026-08-21

The roadmap experiments (`rrf_k`, `rerank_body`, `end_to_end`, `decay_rrf`,
`recency`) were run authoritatively on the **reproducible balanced 100-question
subsample** of `longmemeval-s-s` (seed 42, split `c6178fd0a436`, composition
40 temporal-reasoning / 30 knowledge-update / 20 multi-session / 10
single-session-user; 45,580 stored episodes over 4,556 sessions). Retrieval-only
(no LLM in the query path, ADR-10). Embeddings: `multilingual-e5-small`
(`me5-small:384:int8`, body+summary per F3).

**Two measurement regimes** — this distinction is mandatory for reading the
numbers:

- **Active-now**: the engine queries the full session history (`pit=None`).
  Measures the retrieval *ceiling*. Two harness paths: the standalone
  experiments (`rrf_k`, `rerank_body`, `end_to_end`) via `build_real_corpus`
  (recall@10 0.79) and the SUT runner (`decay_rrf`, `recency`) via the
  `EvaluationRunner` (recall@10 0.674). Same regime, two ingest paths — the
  absolute baselines differ, so cross-harness absolute comparisons are not
  valid; every decision below is delta-based within a single harness.
- **Temporal** (SUT runner): queries state-at-question-date, so episodes after
  the question are invisible. Lower (recall@10 0.18), but the honest
  state-restricted measurement.

All decisions below compare **within a regime and harness** (baseline vs variant
at the same setting), so each is valid.

| Experiment | Regime | Evidence | Decision |
|---|---|---|---|
| `rrf_k` (A5) | active-now | recall@10 **0.790** flat across RRF_K ∈ {10, 20, 40, 60} | **keep_60** — the untuned fusion constant is optimal |
| `rerank_body` (A6) | active-now | baseline 0.790 · summary 0.660 · **body 0.830** | **reopen_rerank** — the summary/subject representation was the culprit behind F2's rejection |
| `end_to_end` (A4) | active-now | recall@10 0.790 · e2e accuracy **0.070** | **reader_bottleneck** — retrieval recovers the session but the top-k summary context cannot support answers |
| `decay_rrf` | active-now (runner) | mvp1_rrf 0.674/0.547 · mvp1_decay 0.674/**0.463** (ndcg −8.3%) | **keep_off** — decay leaves recall@10 flat and degrades ndcg@10 −8.3%; default-OFF confirmed with real evidence |
| `recency` | active-now (runner) | baseline 0.674/0.547; all 9 γ×half-life combos recall@10 **0.674 flat**, ndcg 0.523–0.542 (every combo degrades) | **keep_off** — the boost fires, never moves the top-10, and degrades ndcg@10 in every combo; confirms F1 on the reproducible subsample |

**Correction (2026-08-21, evening).** The first `decay_rrf`/`recency` runs used
the SUT runner's default `pit_queries=True` in temporal mode, which PITs every
query with `state_at(question_date)` — and the recency/decay seams are gated by
`pit is None` (ADR-03), so the seams never fired and the "identical" numbers
(0.178/0.175) were a forced null, not a measurement. Re-run in the active-now
regime (`--no-temporal`), where the seams do fire: decay degrades ndcg@10
−8.3% and recency degrades it −0.5 to −2.4pp in every combo, both with recall@10
flat. Both `keep_off` decisions stand, now with valid evidence. The harness is
fixed so this cannot recur: the runner forces `pit_queries=False` for
`decay_rrf`/`recency` (`_resolve_pit_queries`, ADR-03) and the CLI exposes
`--pit-queries/--no-pit-queries` — a temporal decay/recency run can no longer
silently measure a forced null.

The decisions that are action items:

1. **`reopen_rerank` (A6).** Reranking the full body recovers recall@10 0.83 —
   above the pure-RRF baseline (0.79) and far above the summary rerank (0.66).
   The stage-3 rerank should hydrate the **full body**, not `summary or subject`.
   The latency gate (p95 ≤ 500 ms on the cross-encoder) is still to be verified
   for the longer body texts before the reranker can ship.
2. **`reader_bottleneck` (A4).** Retrieval is *not* the blocker on real data:
   the golden session ranks in the top-10 for 79% of questions, but the
   top-k summary context supports extractive answers only 7% of the time. This
   inverts the synthetic finding and points the next milestone at the
   context-representation / reader step, not the ranking stage.
3. **`keep_60`, `keep_off`, `keep_off`.** The fusion constant (0.790 flat) and
   both default-off seams are confirmed on the real corpus with reproducible
   seeds — no flip.

Each run is reproducible via the commands in [Reproduce](#reproduce) with
`--experiment <name> --corpus lmeb-s --retrieval-only --subsample`.

## Authoritative experiment decisions — 2026-08-22 reader-context

The `reader_context` A/B falsifies the A4 `reader_bottleneck` hypothesis: does
hydrating the **full body** (not the ~200-char summary) close the end-to-end
gap? Measured on the same reproducible 100-question subsample (seed 42, split
`c6178fd0a436`), corpus built **once** and the three assembler modes
(`summary` | `body` | `body_bounded` capped at 2000 chars/episode) measured
over the same facade. Reader: the real `ReaderLLMClient` (`ollama/qwen3:0.6b`,
t=0, seed=42, max_tokens=512) — the H2 bug (the reader discarded the question)
was fixed before this run. Active-now regime (FULL PIT is a later release).

| Mode | recall@10 (ceiling) | end-to-end accuracy |
|---|---|---|
| `summary` (baseline) | 0.790 | **0.070** |
| `body` (full hydration) | 0.790 | 0.090 |
| `body_bounded` (2000 chars) | 0.790 | 0.090 |

**Decision: `keep_summary`.** Hydrating the full body recovers only **2.0pp**
(0.070 → 0.090), far below the `READER_CONTEXT_DELTA_PP` = 10pp flip threshold.
The summary representation is **not** the reader bottleneck.

**The deeper finding.** The ~70pp gap between recall@10 (0.790 — the golden
session ranks in the top-10) and end-to-end accuracy (0.070–0.090) does *not*
close when the reader sees the full body. The bottleneck is therefore not the
context representation. Two candidate causes, both follow-ups: (1) **episode
granularity** — session-level recall is coarse; the golden session is in the
top-10, but the specific answer-bearing episode may not be, so no context
representation can help; (2) **reader quality** — `qwen3:0.6b` is a weak
extractor, and a stronger reader (e.g. `qwen3:14b-q4_K_M`, ~15× slower) might
recover more from either representation. The delta between modes is the honest
signal here; the absolute accuracy is a floor set by the weak reader.

**Caveats.** Reader is the small local model `qwen3:0.6b` (chosen on a timing
probe: ~2s/query vs ~28s for the 14b — 300 LLM calls ≈ 10 min vs 2.3 h); the
extractive double is never presented as "the reader". The `episodes: 0` line in
the report is cosmetic (the real corpus keeps episodes in the DB, not a Python
list). The harness seam (`ContextMode`, `assemble_context`, `batch_body_for`,
`--context-mode`) ships as the product-facing deliverable; the product answer
path keeps the summary representation.

## Authoritative experiment decisions — 2026-08-25 episode-granularity

The `episode_granularity` experiment falsifies the A4 granularity hypothesis:
is the ~70pp gap (recall@10 0.790 → e2e 0.070) caused by the answer-bearing
episode missing from the top-10 even though its golden session ranks? Measured
on the same reproducible 100-question subsample (seed 42, split
`c6178fd0a436`), retrieval-only, active-now. The answer-bearing episode is
located with the n-gram heuristic in `experiments/episode_locator.py`
(verbatim / fragment / single-token; 92/100 localized — the 8 unlocalized are
derived numeric answers excluded from the episode-recall denominator).
Within-session rank is a vector-only approximation (fastembed, role-prefixed):
the engine has no session-restricted recall, so all episodes of the golden
session are scored against the query and the best answer-bearing episode's rank
is read off.

| Metric | Value |
|---|---|
| session-level recall@10 | 0.790 |
| episode-level recall@10 (localized only, n=92) | **0.533** |
| within-session top-1 / top-3 / top-5 | 0.413 / 0.685 / 0.826 |
| answer-in-context rate (fragment ≥ 2 tokens in top-k) | 0.350 |

**Decision: `reader_bottleneck`.** Episode-level recall@10 (0.533) clears the
`EPISODE_LEVEL_RECALL_THRESHOLD` = 0.5 gate: the answer-bearing episode IS
retrieved in a majority of queries, so episode granularity is not the dominant
bottleneck. The remaining loss is downstream — context assembly + reader
extraction — and the conditional two-stage retrieval fix is explicitly **not**
indicated by the plan's decision rule.

**Honest reading of the numbers.** The granularity gap is real but secondary:
session 0.790 → episode 0.533 (≈26pp), and within-session the answer episode is
top-1 only 41% of the time — a two-stage session→episode rerank would recover
part of that. But the dominant loss sits between episode recall (0.533) and
answer-in-context (0.350): the answer-bearing episode reaches the top-10, yet a
distinctive answer fragment appears in the assembled context barely a third of
the time. Follow-ups, in order of expected leverage: (1) **reader quality** —
`qwen3:0.6b` is a weak extractor; (2) **context assembly** — which episodes'
bodies actually make it into the top-k context; (3) two-stage session→episode
re-ranking, only if (1) and (2) do not close the gap.

**Caveats.** Localization is a heuristic, never ground truth — the raw LMEB
dataset exposes no answer→turn mapping. The 8/100 unlocalized derived answers
(e.g. "43" computed from two facts) are excluded from the denominator, not
counted as misses. Within-session rank is vector-only, a local approximation of
the hybrid re-score. Runs are active-now (ADR-03); FULL PIT is a later release.

## Caveats

1. **Subsample.** n≈470–500 questions from `longmemeval-s-s`, not the full
   dataset. These numbers are not a complete leaderboard. The subsample is
   balanced across question types (temporal-reasoning, knowledge-update,
   multi-session, single-session) so the slices are decision-complete, but the
   absolute values are not a full-dataset measurement.
2. **Judge applies only to end-to-end metrics.** The retrieval metrics
   (recall@10, ndcg@10, mrr, precision@10) are computed against the dataset's
   ground-truth golden sessions — **no LLM judge is involved**. The small,
   unvalidated judge (`ollama/qwen2.5:7b`) is used only for the end-to-end
   metrics (`knowledge_update_accuracy`, `fama_gap`), which are 0.0 by design
   in retrieval-only mode. The retrieval numbers do not depend on the judge.
3. **Retrieval-only.** This measures the ranking, not the agent's final answer.
   `knowledge_update_accuracy` and `fama_gap` are 0.0 by design (sanity checks
   without a baseline). The end-to-end value of the product (reader + retrieval)
   is not yet measured — that is the next step.
4. **Cross-encoder rejected.** A cross-encoder rerank was tested
   (`score_source: rrf_rerank`) and **rejected**: it degraded recall@10 to
   0.1057 with 1243 ms p95 latency. The RRF fusion stays the default. The
   `rerank_body` experiment (2026-08-21) re-opens this: scoring the **full
   body** instead of the summary recovers recall@10 0.83, so the rejection is
   specific to the representation, not the reranker.
5. **Two measurement regimes, two harness paths.** The active-now experiments
   and the temporal experiments (0.18) are **not directly comparable** — they
   answer different questions (full-history ceiling vs state-at-question-date).
   Within active-now there are also two harness paths: the standalone
   `build_real_corpus` experiments (0.79) and the SUT runner (0.674) — same
   regime, different ingest, so their absolutes are not comparable either; all
   decisions are delta-based within a single harness. The published single-run
   number above (recall@10 0.1296) is from the older n≈470–500 temporal run and
   is superseded for *decision-making* by the reproducible 100 subsample runs,
   though it remains the larger-sample measurement.

The temporal-reasoning slice (recall@10 0.02) is a **fundamental limitation of
retrieval-only ranking, not a bug**: the engine ranks by textual relevance and
cannot reason about "before/after/at the time of". Temporal reasoning is a
reader/agent capability — the plan is to measure it end-to-end (reader +
retrieval) rather than chase it with retrieval-only tricks.

## How not to compare these numbers

These numbers are **not comparable** to the scores other memory systems
publish, and reading them side by side is misleading. The published scores are
a different metric:

| System | Published score | What it actually measures |
|---|---|---|
| Graphiti (Zep) | 63.8% LongMemEval | end-to-end accuracy, full dataset, strong reader |
| Mem0 | 94.8 LongMemEval (self-reported) | end-to-end accuracy, full dataset |
| Hindsight (Vectorize) | 91.4% LongMemEval (self-reported) | end-to-end accuracy, Gemini-3 Pro reader |
| MemPalace | 96.6% R@5 LongMemEval | verbatim exact-match retrieval, no LLM |
| **Seahorse** | **recall@10 0.13** | **retrieval ranking only, subsample, small judge** |

The differences that make a direct comparison invalid:

1. **Metric.** Seahorse measures the *ranking* of the retrieval stage
   (recall@10 / ndcg@10). The others measure the *final answer* of a full
   agent (reader LLM + retrieval). A system can score high end-to-end with a
   mediocre retriever if its reader is strong — and vice versa.
2. **Coverage.** Seahorse runs on a subsample (n≈470–500) of `longmemeval-s-s`;
   the others report on the full dataset.
3. **Judge.** Seahorse's relevance scores come from a small, unvalidated LLM
   judge. The others use validated judges and strong readers.

A fair comparison would require running the same harness in the same
configuration — retrieval-only, same subsample, same judge — for each system.
Nobody publishes that baseline today. That is exactly why Seahorse ships the
harness in the repo: so the measurement can be checked, not trusted.

## Reproduce

```bash
# Retrieval-only pass — deterministic, no LLM, cheap:
uv sync --extra dev --extra benchmark --extra embeddings
seahorse benchmark experiment embed --corpus lmeb-s --retrieval-only

# Authoritative experiment runs (reproducible 100 subsample, seed 42).
# decay_rrf and recency MUST run active-now (--no-temporal): the recency/decay
# seams are gated by `pit is None` (ADR-03), so a temporal run PITs every query
# and measures a forced null (see the correction note above).
seahorse benchmark experiment rrf_k --corpus lmeb-s --retrieval-only --subsample
seahorse benchmark experiment rerank_body --corpus lmeb-s --retrieval-only --subsample
seahorse benchmark experiment end_to_end --corpus lmeb-s --retrieval-only --subsample
seahorse benchmark experiment decay_rrf --corpus lmeb-s --retrieval-only --no-temporal --subsample
seahorse benchmark experiment recency --corpus lmeb-s --retrieval-only --no-temporal --subsample

# Reader-context A/B (real reader, requires Ollama + the llm extra):
uv sync --extra dev --extra benchmark --extra embeddings --extra llm
seahorse benchmark experiment reader_context --corpus lmeb-s --reader-model ollama/qwen3:0.6b --subsample

# Episode-granularity (retrieval-only, deterministic — the heuristic is pure code):
seahorse benchmark experiment episode_granularity --corpus lmeb-s --retrieval-only --subsample

# Full run with judge LLM (requires Ollama + the llm extra):
uv sync --extra dev --extra benchmark --extra embeddings --extra llm
seahorse benchmark run --adapter lmeb --config s
```

The `benchmark-output/` directory is git-ignored: this document publishes the
curated numbers and the commands to regenerate them, not the artifacts.

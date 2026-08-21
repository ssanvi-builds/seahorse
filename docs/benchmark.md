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

- **Active-now** (standalone experiments): the engine queries the full session
  history (`pit=None`). Measures the retrieval *ceiling*.
- **Temporal** (SUT runner experiments): queries state-at-question-date, so
  episodes after the question are invisible. Lower, but the honest
  state-restricted measurement.

The absolute recall@10 differs across regimes (0.79 active-now vs 0.18
temporal). All decisions below compare **within a regime** (baseline vs variant
at the same setting), so each is valid.

| Experiment | Regime | Evidence | Decision |
|---|---|---|---|
| `rrf_k` (A5) | active-now | recall@10 **0.790** flat across RRF_K ∈ {10, 20, 40, 60} | **keep_60** — the untuned fusion constant is optimal |
| `rerank_body` (A6) | active-now | baseline 0.790 · summary 0.660 · **body 0.830** | **reopen_rerank** — the summary/subject representation was the culprit behind F2's rejection |
| `end_to_end` (A4) | active-now | recall@10 0.790 · e2e accuracy **0.070** | **reader_bottleneck** — retrieval recovers the session but the top-k summary context cannot support answers |
| `decay_rrf` | temporal | mvp1_rrf 0.178/0.175 · mvp1_decay 0.178/0.175 | **keep_off** — decay changes nothing (ku slice 0.429 both) |
| `recency` | temporal | baseline 0.178/0.175; all 9 γ×half-life combos identical | **keep_off** — the boost never moves the top-10; confirms F1 on the reproducible subsample |

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
5. **Two measurement regimes.** The active-now experiments (0.79) and the
   temporal experiments (0.18) are **not directly comparable** — they answer
   different questions (full-history ceiling vs state-at-question-date). The
   published single-run number above (recall@10 0.1296) is from the older
   n≈470–500 temporal run and is superseded for *decision-making* by the
   reproducible 100 subsample runs, though it remains the larger-sample
   measurement.

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

# Authoritative experiment runs (reproducible 100 subsample, seed 42):
seahorse benchmark experiment rrf_k --corpus lmeb-s --retrieval-only --subsample
seahorse benchmark experiment rerank_body --corpus lmeb-s --retrieval-only --subsample
seahorse benchmark experiment end_to_end --corpus lmeb-s --retrieval-only --subsample
seahorse benchmark experiment decay_rrf --corpus lmeb-s --retrieval-only --subsample
seahorse benchmark experiment recency --corpus lmeb-s --retrieval-only --subsample

# Full run with judge LLM (requires Ollama + the llm extra):
uv sync --extra dev --extra benchmark --extra embeddings --extra llm
seahorse benchmark run --adapter lmeb --config s
```

The `benchmark-output/` directory is git-ignored: this document publishes the
curated numbers and the commands to regenerate them, not the artifacts.

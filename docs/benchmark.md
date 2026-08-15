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

## Caveats

1. **Subsample.** n≈470–500 questions from `longmemeval-s-s`, not the full
   dataset. These numbers are not a complete leaderboard.
2. **Small, unvalidated judge.** Relevance scores depend on a small LLM judge
   without human validation. Treat the absolute values as indicative.
3. **Retrieval-only.** This measures the ranking, not the agent's final answer.
   `knowledge_update_accuracy` and `fama_gap` are 0.0 by design (sanity checks
   without a baseline).
4. **Cross-encoder rejected.** A cross-encoder rerank was tested
   (`score_source: rrf_rerank`) and **rejected**: it degraded recall@10 to
   0.1057 with 1243 ms p95 latency. The RRF fusion stays the default.

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

# Full run with judge LLM (requires Ollama + the llm extra):
uv sync --extra dev --extra benchmark --extra embeddings --extra llm
seahorse benchmark run --adapter lmeb --config s
```

The `benchmark-output/` directory is git-ignored: this document publishes the
curated numbers and the commands to regenerate them, not the artifacts.

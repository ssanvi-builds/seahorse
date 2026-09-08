# Related work

Seahorse is one of many approaches to agent memory. This page records the
landscape it was built against, with a primary source for every external
claim. External facts below were last verified on 2026-09-07.

## Open-source memory systems

| System | License | Memory model | Verified notes |
|---|---|---|---|
| [mem0](https://github.com/mem0ai/mem0) | Apache-2.0 (open-core) | LLM-extracted facts; vector + optional graph | Published LongMemEval numbers come from the managed platform; benchmark-driving features (Contextual ADD, Custom Instructions, Temporal Reasoning, Memory Decay; Graph memory and Dream on paid tiers) are not in the open-source library. mem0's own eval suite shows ~91% open-source vs 94.4% platform. Reproducibility is disputed ([issue #2800](https://github.com/mem0ai/mem0/issues/2800)). Sources: [memory-benchmarks](https://github.com/mem0ai/memory-benchmarks), [mem0 blog 2026-05-12](https://mem0.ai/blog/introducing-temporal-reasoning-in-mem0), [pricing](https://mem0.ai/pricing). |
| [Letta](https://github.com/letta-ai/letta) (ex-MemGPT) | Apache-2.0 | Memory blocks + archival passages, agent-managed via tools | Memory is runtime-bound: adopting Letta means adopting its agent runtime. Archival passages are excluded from agent-export files ([docs](https://docs.letta.com/v1-sdk/concepts/agent-file/index.md)); passages mutate in place with no prior-value history ([docs](https://docs.letta.com/api/resources/agents/subresources/passages/)). |
| [Zep / Graphiti](https://github.com/getzep/graphiti) | Graphiti Apache-2.0; Zep product proprietary | Bi-temporal knowledge graph (valid_at / invalid_at per fact) | The self-hostable Zep Community Edition was discontinued 2025-04-02 ([announcement](https://blog.getzep.com/announcing-a-new-direction-for-zeps-open-source-strategy/), [PR #390](https://github.com/getzep/zep/pull/390)); the product ships cloud-only (managed, BYOK, enterprise BYOC). Graphiti remains an open-source engine, not a hosted memory service. |
| [Cognee](https://github.com/topoteretes/cognee) | Apache-2.0 | Knowledge graph + vector, Postgres-native | Multi-LLM via LiteLLM; no published LongMemEval score at review time. |
| [A-MEM](https://github.com/agiresearch/A-mem) | MIT | Zettelkasten-style atomic notes | Agentic note evolution; no formal forgetting or temporal model. |
| [MemOS](https://github.com/MemTensor/MemOS) | Apache-2.0 | Three-layer (plaintext / activation / parametric) | Unifies memory types behind one abstraction; high operational complexity. |
| [Hindsight](https://github.com/vectorize-io/hindsight) | MIT | Four biomimetic banks with multi-strategy recall | Self-reported 91.4% LongMemEval with a Gemini-3 Pro reader ([benchmarks README](https://github.com/vectorize-io/hindsight-benchmarks/blob/main/README.md)). |
| [LangMem](https://github.com/langchain-ai/langmem) | MIT | Semantic / episodic / procedural SDK | Tied to the LangGraph ecosystem. |
| [MemPalace](https://github.com/MemPalace/mempalace) | MIT | Local-first verbatim storage | Self-reported 96.6% R@5 LongMemEval with no LLM ([benchmarks README](https://github.com/MemPalace/mempalace/blob/develop/benchmarks/README.md)) — a retrieval-recall metric, not end-to-end QA (an independent tester reports ~82.6% QA, [issue #367](https://github.com/MemPalace/mempalace/issues/367)). |
| [claude-mem](https://github.com/thedotmack/claude-mem) | README states Apache-2.0; LICENSE file is AGPL-3.0 (discrepancy) | Compressed session observations (SQLite + FTS5 + Chroma) | Local session memory for coding agents; no public benchmark. |

## Benchmarks, and why we do not compare scores directly

- **LOCOMO's answer key is imperfect.** An independent audit found 99
  score-corrupting errors in 1,540 questions (6.4%), capping the theoretical
  ceiling near 93.6% ([Penfield Labs](https://penfieldlabs.substack.com/p/we-audited-locomo-64-of-the-answer),
  [audit report](https://github.com/dial481/locomo-audit/blob/main/AUDIT_REPORT.md)).
- **Reproducibility disputes are the norm.** Mem0's published numbers do not
  reproduce against the public code ([issue #2800](https://github.com/mem0ai/mem0/issues/2800)),
  and the Mem0–Zep exchange over judge prompts and partial credit is
  documented on both sides ([Zep's critique](https://blog.getzep.com/state-of-the-art-memory-lower-latency-more-accurate-than-mem0/)).
- **Embedding scores do not predict memory retrieval.** LMEB shows MTEB-style
  passage-retrieval performance and long-horizon memory retrieval measure
  orthogonal capabilities ([arXiv:2603.12572](https://arxiv.org/abs/2603.12572)).
- **Every published end-to-end score relies on an unvalidated LLM judge.**
  The official LongMemEval harness decides correctness entirely with an LLM
  judge ([evaluate_qa.py](https://github.com/xiaowu0162/LongMemEval)); vendor
  blogs use GPT-4o-class judges; none publishes human validation of its judge.
  Judge quality is measurable: in the MT-Bench study, a GPT-4-class judge agreed
  with human experts on ~85% of pairwise comparisons, roughly matching
  agreement between humans themselves (~81%) ([arXiv:2306.05685](https://arxiv.org/abs/2306.05685)),
  while smaller models judge materially worse.
- **Same-harness comparisons exist but are new and narrow.** Mid-2026 brought
  unified reruns of multiple memory systems ([arXiv:2604.01707](https://arxiv.org/abs/2604.01707),
  [MemDelta, arXiv:2606.29914](https://arxiv.org/abs/2606.29914)) — none of
  them measures the retrieval stage under a shared harness with a
  human-validated judge. That gap is what Seahorse's harness
  ([docs/benchmark.md](benchmark.md)) targets, with its caveats published
  next to the numbers.

## Interchange formats

Several open specifications for agent-memory interchange exist or are
proposed (Universal Memory Protocol, MacPaw's Portable Memory, the AIMEM
IETF draft, MIF, PAM, OMS). None has vendor adoption or a standards-body
backing yet. F3.1 ([docs/f3.1-format.md](f3.1-format.md)) is the only
markdown-native one: human-readable frontmatter, bi-temporal timestamps,
append-only supersession with a recorded reason, and an `x-*` extension
channel. Interchange in both directions is a stated design goal: losses on
import must be declared, never silent.

## How we keep this page honest

Every external claim above carries a primary source. Numbers Seahorse cites
from others are labeled self-reported where the vendor measured itself.
Seahorse's own numbers, caveats, and reproduction commands live in
[docs/benchmark.md](benchmark.md).
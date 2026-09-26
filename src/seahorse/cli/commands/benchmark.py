"""``benchmark`` group — ``benchmark run|experiment|list|adapters``.

Pure move of the ``benchmark_app`` block from ``cli.app``: the sub-Typer is
created here and attached to the app handed to ``register`` (the app stays a
single Typer instance owned by the composition root). The command bodies
lazy-import ``seahorse.benchmark.*`` exactly as before — the harness (and its
heavy embedding/LLM dependencies) must not load for every other command.
"""

from __future__ import annotations

import typer


def register(app: typer.Typer) -> None:
    """Attach the ``benchmark`` group in the position its block occupied."""
    benchmark_app = typer.Typer(help="LMEB benchmark harness.")
    app.add_typer(benchmark_app, name="benchmark")

    @benchmark_app.command("run")
    def benchmark_run_cmd(
        ctx: typer.Context,
        adapter: str = typer.Option("lmeb", "--adapter", help="Dataset adapter (e.g. lmeb)."),
        dataset_config: str = typer.Option("s", "--config", help="Dataset config (e.g. s)."),
        reader_model: str = typer.Option(
            "ollama/qwen3:1.7b", "--reader-model", help="Reader LLM (t=0, seed=42)."
        ),
        judge_model: str = typer.Option(
            "ollama/qwen2.5:7b", "--judge-model", help="Judge LLM (family-disjoint from reader)."
        ),
        temporal: bool = typer.Option(
            False, "--temporal", help="Temporal mode (source_type=human)."
        ),
        output_dir: str = typer.Option("benchmark-output", "--output-dir"),
        top_k: int = typer.Option(10, "--top-k", "-k"),
        score_source: str = typer.Option(
            "mvp1_rrf",
            "--score-source",
            help="mvp1_rrf | mvp1_rrf_recency | mvp1_decay | rrf_rerank.",
        ),
        recency_gamma: float | None = typer.Option(
            None,
            "--recency-gamma",
            help="Recency max boost at age 0 (pairs with --recency-half-life).",
        ),
        recency_half_life: float | None = typer.Option(
            None,
            "--recency-half-life",
            help="Recency half-life in days (pairs with --recency-gamma).",
        ),
        decay_half_life: float | None = typer.Option(
            None,
            "--decay-half-life",
            help="Decay half-life in days for all cognitive types (default-OFF when unset).",
        ),
        embed_mode: str = typer.Option(
            "body+summary",
            "--embed-mode",
            help="Passage text to embed: body+summary (default) | body (baseline).",
        ),
        rerank_enable: bool = typer.Option(
            False,
            "--rerank-enable",
            help="Cross-encoder rerank (opt-in, score_source=rrf_rerank).",
        ),
        context_mode: str = typer.Option(
            "summary",
            "--context-mode",
            help=(
                "Reader context representation: summary (default) | body | body_bounded "
                "(the reader-context A/B axis)."
            ),
        ),
    ) -> None:
        """Run the LMEB benchmark harness (exit 0=Pass / 10=Fail / 3=Tampered)."""
        from seahorse.benchmark.cli import run_benchmark

        code = run_benchmark(
            adapter=adapter,
            dataset_config=dataset_config,
            reader_model=reader_model,
            judge_model=judge_model,
            temporal=temporal,
            output_dir=output_dir,
            top_k=top_k,
            score_source=score_source,
            recency_gamma=recency_gamma,
            recency_half_life=recency_half_life,
            decay_half_life=decay_half_life,
            embed_mode=embed_mode,
            rerank_enable=rerank_enable,
            context_mode=context_mode,
        )
        raise typer.Exit(code=code)

    @benchmark_app.command("experiment")
    def benchmark_experiment_cmd(
        ctx: typer.Context,
        experiment: str = typer.Argument(
            ...,
            help=(
                "recency | rerank | embed | decay_rrf | batch | entity_centric | "
                "multi_hop | decay | skills | rrf_k | rerank_body | end_to_end | "
                "reader_context | episode_granularity | reader_quality | "
                "context_assembly | two_stage_retrieval (which experiment to run)."
            ),
        ),
        corpus: str = typer.Option(
            "synthetic",
            "--corpus",
            help=(
                "synthetic (CI mechanical verification) | lmeb-s (authoritative) | "
                "claude-mem (real batch corpus)."
            ),
        ),
        output_dir: str = typer.Option("benchmark-output", "--output-dir"),
        reader_model: str = typer.Option(
            "ollama/qwen3:1.7b", "--reader-model", help="Reader LLM (t=0, seed=42)."
        ),
        strong_reader_model: str = typer.Option(
            "ollama/deepseek-v4-flash:0731-cloud",
            "--strong-reader-model",
            help=(
                "Strong reader LLM for the reader_quality A/B (the weak baseline is "
                "--reader-model; the strong candidate is this model)."
            ),
        ),
        judge_model: str = typer.Option(
            "ollama/qwen2.5:7b", "--judge-model", help="Judge LLM (family-disjoint from reader)."
        ),
        top_k: int = typer.Option(10, "--top-k", "-k"),
        temporal: bool = typer.Option(
            True, "--temporal/--no-temporal", help="Temporal ingestion (source_type=human)."
        ),
        pit_queries: bool = typer.Option(
            True,
            "--pit-queries/--no-pit-queries",
            help=(
                "Query active-now (pit=None) instead of state-at-question-date. "
                "Forced OFF for decay_rrf/recency: the recency/decay seams are gated "
                "by `pit is None` (ADR-03), so a PIT query would measure a forced null."
            ),
        ),
        retrieval_only: bool = typer.Option(
            False,
            "--retrieval-only",
            help=(
                "Retrieval-only pass: deterministic stub reader (no Ollama). The "
                "decision metrics (recall@10/ndcg@10) never consume the reader's answer "
                "— identical decision numbers, zero LLM cost."
            ),
        ),
        subsample: bool = typer.Option(
            True,
            "--subsample/--no-subsample",
            help=(
                "Apply the reproducible balanced 100-question subsample to the "
                "LMEB-S corpus (the documented compromise; the full-corpus ingest "
                "hangs on FTS5 and runs overnight). Default ON."
            ),
        ),
        context_mode: str = typer.Option(
            "summary",
            "--context-mode",
            help=(
                "Reader context representation: summary (default) | body | body_bounded "
                "(the reader-context A/B axis; the reader_context experiment runs all "
                "three and ignores this flag)."
            ),
        ),
    ) -> None:
        """Run an experiment and print the sweep table + decision."""
        from seahorse.benchmark.experiments.runner import (
            render_experiment_report,
            run_experiment,
        )
        from seahorse.benchmark.harness.reader_llm import StubReaderLLM

        report = run_experiment(
            experiment=experiment,
            corpus=corpus,
            output_dir=output_dir,
            reader_model=reader_model,
            strong_reader_model=strong_reader_model,
            judge_model=judge_model,
            top_k=top_k,
            temporal=temporal,
            pit_queries=pit_queries,
            reader_llm=StubReaderLLM() if retrieval_only else None,
            subsample=subsample,
            context_mode=context_mode,
        )
        typer.echo(render_experiment_report(report))

    @benchmark_app.command("list")
    def benchmark_list_cmd(ctx: typer.Context) -> None:
        """List available dataset adapters."""
        from seahorse.benchmark.cli import list_benchmarks

        for name in list_benchmarks():
            typer.echo(name)

    @benchmark_app.command("adapters")
    def benchmark_adapters_cmd(ctx: typer.Context) -> None:
        """List available SUT adapters."""
        from seahorse.benchmark.cli import list_adapters

        for name in list_adapters():
            typer.echo(name)
# Contributing to Seahorse

Thanks for considering a contribution. This project is small and intentionally
scoped — before opening a large PR, it usually pays to open an issue first and
discuss the direction.

## Development setup

Requirements: Python ≥ 3.11, [uv](https://docs.astral.sh/uv/).

```bash
# Install the project with the dev extras:
uv sync --extra dev

# Optional extras:
uv sync --extra dev --extra embeddings   # hybrid semantic retrieval (ONNX)
uv sync --extra dev --extra llm          # multi-LLM extraction path (LiteLLM)
uv sync --extra dev --extra benchmark    # LMEB-S benchmark harness
```

## Checks

Everything below runs against the default `uv sync --extra dev` environment.

```bash
# Test suite (includes the ≥80% coverage gate):
uv run pytest

# Lint:
uv run ruff check src tests

# Type check:
uv run mypy src
```

CI runs the test suite (with the coverage gate), ruff, and mypy on every push
and pull request. There is also a separate CI job for the LLM extraction path
(`ci-llm-gate.yml`) that requires a real Ollama server and runs only when
`SEAHORSE_RUN_LLM_TESTS=1` is set:

```bash
SEAHORSE_RUN_LLM_TESTS=1 uv run pytest -m llm_gate
```

Integration scripts (used by CI, run manually with a local install):

- `scripts/e2e-fresh-user.sh` — end-to-end flow from a clean, isolated HOME.
- `scripts/e2e-matrix.sh` — the same flow across environment combinations.
- `scripts/stress-core.sh` — load test with latency budgets.

## Pull request workflow

1. Fork the repository and create a feature branch.
2. Make your change. Follow the existing code style: small files and functions,
   immutable data patterns, explicit error handling, descriptive names.
3. Add or update tests. The coverage gate is ≥80% and is enforced by CI; new
   behaviour needs tests, not just a passing suite.
4. Run the checks above locally and make sure they pass.
5. Open a pull request against `main` with a clear description of what changed
   and why, plus a short test plan.

## Signing the CLA

External contributions are accepted under a Contributor License Agreement
([CLA.md](CLA.md)), adapted from the Apache Software Foundation's ICLA. You
sign once, and the signature covers all your present and future contributions
to this project.

Signing costs one comment. In your first pull request, add a comment
containing:

```
I have read the CLA at CLA.md and agree to its terms — <legal name> (<github handle>)
```

Use your legal name (or the name your employer uses for you) — not a pseudonym
— and your GitHub handle. Alternatively, email the contact address listed on
the repository profile with the same statement. Signatures are recorded
publicly in the pull request thread, so the record is auditable by anyone.

Maintainers will not merge an external pull request until it carries a valid
signature. If anything in the agreement is unclear, ask in your issue or PR —
questions before signing are welcome.

## Licensing of contributions

The project is licensed Apache-2.0 (see [LICENSE](LICENSE)), and your
contributions are licensed to recipients under the same terms — that is the
substance of the CLA above.

One boundary matters before you bring code or text from another project: some
memory systems this project compares against are licensed AGPL-3.0. Seahorse
studies those tools at the mechanism level — their ideas, measured results,
and design choices are fair input — but **line-level reuse is never
acceptable**: no AGPL code, configuration, or text may be copied into this
repository, and an idea adopted from an AGPL project must be re-implemented
from its public description. A pull request containing AGPL-licensed code will
be rejected. The full reasoning lives in
[docs/related-work.md](docs/related-work.md) (License posture).

## Commit conventions

- Use [Conventional Commits](https://www.conventionalcommits.org/): `feat:`,
  `fix:`, `refactor:`, `docs:`, `test:`, `chore:`, `perf:`, `ci:`.
- Do **not** add `Co-Authored-By` or AI attribution trailers to commits.
- Keep each commit scoped to a single logical change.

## Code style

- English for code, comments, and commit messages.
- No hardcoded values; use named constants or configuration.
- Handle errors explicitly at every level; never swallow them silently.
- Do not add comments that restate what the code does — use descriptive names,
  and reserve comments for the non-obvious "why".

## Your first contribution

A short path for a first change:

1. Open an issue describing the problem or the improvement, so the direction
   is agreed before any code is written.
2. Fork and create a feature branch (`git checkout -b <your-branch>`).
3. Make the change and add or update tests; then run `uv run pytest`,
   `uv run ruff check src tests`, and `uv run mypy src` locally.
4. Commit with Conventional Commits and open the pull request (see the
   workflow above).
5. If this is your first contribution, sign the CLA in a comment on that PR.

A maintainer takes it from there.

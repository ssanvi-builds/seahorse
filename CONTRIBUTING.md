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

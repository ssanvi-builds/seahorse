---
name: Bug report
about: Report a problem with Seahorse
title: "[bug] "
labels: bug
assignees: ""
---

## Description

A clear and concise description of the bug.

## Steps to reproduce

1. …
2. …
3. …

## Expected behaviour

What you expected to happen.

## Actual behaviour

What actually happened. Include the full error output and, if relevant, the exit
code (Seahorse uses structured exit codes and a `{"error": ...}` envelope).

## Environment

- Seahorse version: (e.g. `v0.5.1`, or `uv run seahorse --version`)
- Python version:
- Install method: (`uv tool install .` / `uv sync` / other)
- OS:
- Extras installed: (`embeddings` / `llm` / `benchmark` / none)

## Additional context

- Vault contents are **not** sensitive by default, but redact any API keys or
  personal data before pasting.
- Any related issue or PR.

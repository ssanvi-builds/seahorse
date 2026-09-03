# Demo vault (fictional)

A 105-episode F3.1 demo vault, **entirely fictional** — the company
(Northwind Analytics), the people, the projects (Atlas, Beacon, Cinder, Drift, Ember), and every fact.
Nothing here is real user memory. Safe for public screenshots and docs.

Regenerate it:

```bash
python3 generate_demo_vault.py
```

The vault contains the shape of a real working memory: hand-written showcase
notes, supersession chains (`supersedes` / `supersedes_reason`), project
clusters, session notes, preferences, and runbooks.

## Showcase notes (hand-written, not generated)

- `2026-05-10-persona-home-city.md` — the old episode: `invalid_at` set by the
  correction, body kept untouched (append-only).
- `2026-08-30-persona-home-city.md` — the new episode: `supersedes` anchors the
  chain, `supersedes_reason: correction`.

A parser ingesting this folder should report the Barcelona home-city episode as
active and the Madrid one as invalidated-but-preserved.

## Graph

![Demo vault memory graph](graph.svg)

Static render of the vault's memory graph (nodes colored by `cognitive_type`,
red edges = `supersedes` links). Regenerate the render after editing the vault:

```bash
python3 render_graph.py
```

For an interactive version (zoom, pan, drag, tooltips), open `graph.html` in a
browser — self-contained, no dependencies.

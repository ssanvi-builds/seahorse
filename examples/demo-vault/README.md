# Demo vault (fictional)

A 115-note F3.1 demo vault, **entirely fictional** — the company
(Northwind Analytics), the people, the projects (Atlas, Beacon, Cinder, Drift, Ember), and
every fact. Nothing here is real user memory. Safe for public screenshots and
docs.

Regenerate it:

```bash
python3 generate_demo_vault.py
```

The vault mirrors what Seahorse 1.0.0 actually writes into a real vault:

- `Memory/*.md` — 100 notes: 92 episodes (the
  `seahorse materialize --mode all` view — agent memories, observer-captured
  session turns titled `... [session_tag:n]`, and CLI corrections) plus the
  8 dense notes `seahorse consolidate` distills from
  repeated session topics (each carries a `merge` supersession back to its
  most recent source; sources are left valid).
- Vault root — the human layer: 13 hub notes imported once by
  `seahorse frontmatter migrate` (their `created_at`/`valid_at` is the legacy
  file's mtime) and the hand-curated showcase pair below.
- Supersede chains: 4 corrections (`improve`) — 30k→40k msg/s, postcode→H3,
  90→180-day audit retention, hourly→15-minute refresh. The invalidated roots
  keep their bodies and gain `invalid_at` — history is never rewritten.

## Showcase notes (hand-curated mirror)

- `2026-05-10-persona-home-city.md` — the old episode: `invalid_at` set by the
  correction, body kept untouched (append-only).
- `2026-08-30-persona-home-city.md` — the new episode: `supersedes` anchors the
  chain, `supersedes_reason: correction`.

A parser ingesting this folder should report the Barcelona home-city episode as
active and the Madrid one as invalidated-but-preserved.

## Graph

![Demo vault memory graph](graph.svg)

Static render of the vault's memory graph (nodes colored by `cognitive_type`,
red edges = `supersedes` links; the ringed nodes are the `Memory/` notes
`consolidate` distilled). Regenerate the render after editing the vault:

```bash
python3 render_graph.py
```

For an interactive version (zoom, pan, drag, tooltips), open `graph.html` in a
browser — self-contained, no dependencies.

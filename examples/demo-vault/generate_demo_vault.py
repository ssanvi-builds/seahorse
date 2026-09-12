#!/usr/bin/env python3
"""Regenerate the fictional F3.1 demo vault — v1.0.0-authentic.

Deterministic (fixed seed): the same output on every run. Everything in
here is invented — no real person, company, or project. Safe for public
screenshots.

Usage:  python3 generate_demo_vault.py

Layout (mirrors what a real Seahorse 1.0.0 vault looks like):

- ``Memory/*.md`` — everything the engine writes. The episodes (the
  ``seahorse materialize --mode all`` view: agent memories, observer
  turns, CLI corrections) plus the dense notes ``seahorse consolidate``
  distills from repeated session topics.
- vault root — the human layer only: the hub notes imported by
  ``seahorse frontmatter migrate`` (``created_at`` = the legacy file's
  mtime) and the hand-curated Madrid→Barcelona showcase pair.

On-disk format decisions, all verified against the shipped CLI and the
real serializer (see demo_content.py for the content-side notes):

- field order = the Episode contract order; ``invalid_at`` on an
  invalidated note is appended AFTER ``tags`` (the invalidate-merge
  artifact, reproduced as-is);
- provenance key order is alphabetical for engine notes (DB round-trip
  via ``provenance_json(sort_keys=True)``) and literal insertion order
  for migrator hubs (direct write, no DB round-trip);
- timestamps: ISO-8601 ``Z``, single-quoted, microseconds shown only
  when nonzero;
- scalars: plain unless ambiguous (": ", " #", YAML indicator, parses as
  bool/number/timestamp) → single quotes; embedded newlines → double
  quotes with ``\\n`` escapes (ruamel round-trip style);
- ``tags: []`` on every engine/migrator note (the DB does not persist
  tags).

derive_summary / build_title / render_turn_body / slugify are faithful
mirrors of the shipped implementations so the demo stays standalone.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import pathlib
import random
import re

import demo_content as C

HERE = pathlib.Path(__file__).resolve().parent
MEMORY = HERE / "Memory"
SEED = 20260903

SCHEMA_VERSION = "1.0.0"
AGENT_ID = "seahorse/claude-code"
MODEL_USED = "claude-sonnet-5"
MIGRATOR_AGENT_ID = "seahorse/migrator"
SUMMARY_MAX_CHARS = 200
TITLE_MAX_CHARS = 160
BODY_MAX_CHARS = 8000

# The one-time `seahorse frontmatter migrate` run that imported the hubs.
MIGRATION_WHEN = dt.datetime(2026, 1, 10, 9, 2, 11, tzinfo=dt.UTC)
# The hand-curated showcase pair (bodies locked; frontmatter regenerated).
SHOWCASE_FILES = {
    "2026-05-10-persona-home-city.md",
    "2026-08-30-persona-home-city.md",
}
MADRID_WHEN = dt.datetime(2026, 5, 10, 9, 41, 7, tzinfo=dt.UTC)
CORRECTION_WHEN = dt.datetime(2026, 8, 30, 16, 22, 5, tzinfo=dt.UTC)

UTC = dt.UTC
rng = random.Random(SEED)

_used_slugs: set[str] = set()
_day_counters: dict[dt.date, int] = {}
_agent_sessions: dict[dt.date, str] = {}

# ---------------------------------------------------------------- cleanup --

MEMORY.mkdir(exist_ok=True)
for stale in HERE.glob("*.md"):
    if stale.name not in SHOWCASE_FILES and stale.name != "README.md":
        stale.unlink()
for stale in MEMORY.glob("*.md"):
    stale.unlink()

# ------------------------------------------------------------------ helpers --


def _clock(day: dt.date) -> dt.datetime:
    """created_at for a note: morning-of-day, strictly increasing per day."""
    n = _day_counters.get(day, 0)
    _day_counters[day] = n + 1
    base = dt.datetime.combine(day, dt.time(9, 0), tzinfo=UTC)
    offset = dt.timedelta(
        minutes=47 * n + rng.randint(0, 20),
        seconds=rng.randint(0, 59),
        microseconds=rng.randrange(1, 1_000_000),
    )
    return base + offset


def _z(when: dt.datetime) -> str:
    """ISO-8601 Z timestamp; microseconds only when nonzero (contract style)."""
    return when.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _uuid7(when: dt.datetime) -> str:
    """Deterministic RFC 9562 UUIDv7 (version nibble 7, variant 89ab)."""
    ms = int(when.timestamp() * 1000)
    h = (
        f"{ms:012x}"
        f"7{rng.getrandbits(12):03x}"
        f"{rng.choice('89ab')}{rng.getrandbits(12):03x}"
        f"{rng.getrandbits(48):012x}"
    )
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def _uuid4() -> str:
    """Deterministic UUIDv4 (Claude Code session ids are v4)."""
    h = (
        f"{rng.getrandbits(32):08x}{rng.getrandbits(16):04x}"
        f"4{rng.getrandbits(12):03x}"
        f"{rng.choice('89ab')}{rng.getrandbits(12):03x}"
        f"{rng.getrandbits(48):012x}"
    )
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def _tool_use_id() -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    return "toolu_01" + "".join(rng.choices(alphabet, k=22))


_YAML_INDICATORS = set("-?:,[]{}#&*!|>'\"%@`")


def _needs_quotes(s: str) -> bool:
    if not s or s != s.strip():
        return True
    if s[0] in _YAML_INDICATORS or s.endswith(":"):
        return True
    if ": " in s or " #" in s:
        return True
    if s.lower() in ("true", "false", "null", "~", "yes", "no", "on", "off"):
        return True
    try:
        int(s, 10)
        return True
    except ValueError:
        pass
    try:
        float(s)
        return True
    except ValueError:
        pass
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}([Tt ].*)?$", s))


def _y(s: str) -> str:
    """YAML scalar, ruamel round-trip style (plain / single / double)."""
    if "\n" in s:
        esc = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{esc}"'
    if _needs_quotes(s):
        return "'" + s.replace("'", "''") + "'"
    return s


def _pv(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, float):
        return repr(v)
    return _y(str(v))


def _slug(subject: str) -> str:
    """Mirror of frontmatter.materialize.slugify."""
    slug = re.sub(r"[^a-z0-9]+", "-", subject.lower())
    return re.sub(r"^-+|-+$", "", slug)


def _memory_path(subject: str, ep_id: str) -> pathlib.Path:
    slug = _slug(subject)
    cand = slug
    if cand in _used_slugs:
        cand = f"{slug}-{ep_id.replace('-', '')[:8]}"
    if cand in _used_slugs:  # collision even on the id8 suffix — fail loud
        raise RuntimeError(f"slug collision not modeled: {subject!r}")
    _used_slugs.add(cand)
    return MEMORY / f"{cand}.md"


def _strip_h1(body: str) -> str:
    """Mirror of write_path.extract._strip_h1."""
    lines = body.splitlines()
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and lines[i].lstrip().startswith("# "):
        i += 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    return "\n".join(lines[i:]).strip()


def _first_sentence(text: str) -> str:
    """Mirror of write_path.extract._first_sentence."""
    for i, ch in enumerate(text):
        if ch in ".!?" and (i + 1 >= len(text) or text[i + 1].isspace()):
            return text[: i + 1].strip()
    return text.strip()


def _derive_summary(body: str) -> str | None:
    """Mirror of write_path.extract.derive_summary."""
    content = _strip_h1(body)
    if not content:
        return None
    sentence = _first_sentence(content)
    if not sentence:
        return None
    return sentence[:SUMMARY_MAX_CHARS]


def _build_title(prompt: str, session_tag: str, prompt_number: int) -> str:
    """Mirror of observe.batcher.build_title."""
    first_line = prompt.split("\n", 1)[0].strip() or "untitled"
    tag = f"[{session_tag}:{prompt_number}]"
    budget = max(1, TITLE_MAX_CHARS - len(tag) - 1)
    truncated = first_line.encode()[:budget].decode("utf-8", "ignore")
    return f"{truncated} {tag}"


def _render_turn_body(title: str, prompt: str, events) -> str:
    """Mirror of observe.batcher.render_turn_body."""
    lines = ["# " + title, "", "## User prompt", "", prompt]
    for tool_name, tool_input, tool_response in events:
        lines.append("")
        lines.append(f"### {tool_name}")
        lines.append(f"tool_use_id: {_tool_use_id()}")
        lines.append(f"input: {tool_input}")
        lines.append(f"response: {tool_response}")
    return "\n".join(lines)[:BODY_MAX_CHARS]


def _write_note(
    path: pathlib.Path,
    *,
    ep_id: str,
    created_at: dt.datetime,
    provenance: dict,
    body: str,
    valid_at: dt.datetime | None = None,
    invalid_at: dt.datetime | None = None,
    invalid_at_last: bool = False,
    supersedes: str | None = None,
    supersedes_reason: str | None = None,
    cognitive_type: str | None = None,
    source_type: str | None = None,
    title: str | None = None,
    summary: str | None = None,
) -> None:
    """One F3.1 note: `---\\n{fm}\\n---\\n{body}` in Episode field order."""
    fm = [
        f"id: {ep_id}",
        f"created_at: '{_z(created_at)}'",
        f"schema_version: {SCHEMA_VERSION}",
        "provenance:",
    ]
    fm += [f"  {k}: {_pv(v)}" for k, v in provenance.items()]
    if valid_at is not None:
        fm.append(f"valid_at: '{_z(valid_at)}'")
    if invalid_at is not None and not invalid_at_last:
        fm.append(f"invalid_at: '{_z(invalid_at)}'")
    if supersedes is not None:
        fm.append(f"supersedes: {supersedes}")
        fm.append(f"supersedes_reason: {supersedes_reason}")
    if cognitive_type is not None:
        fm.append(f"cognitive_type: {cognitive_type}")
    if source_type is not None:
        fm.append(f"source_type: {source_type}")
    if title is not None:
        fm.append(f"title: {_y(title)}")
    if summary is not None:
        fm.append(f"summary: {_y(summary)}")
    fm.append("tags: []")
    if invalid_at is not None and invalid_at_last:
        fm.append(f"invalid_at: '{_z(invalid_at)}'")
    path.write_text("---\n" + "\n".join(fm) + "\n---\n" + body, encoding="utf-8")


def _agent_session(day: dt.date) -> str:
    if day not in _agent_sessions:
        _agent_sessions[day] = _uuid4()
    return _agent_sessions[day]


counts = {"agent": 0, "observer": 0, "improve": 0, "consolidated": 0,
          "hubs": 0, "showcase": 0}


# ------------------------------------------------------------ write paths --


def agent_episode(day, title, body, *, ctype, valid_day=None):
    """MCP `remember` with LLM extraction (wire profile)."""
    when = _clock(day)
    provenance = {  # alphabetical — the DB round-trip sorts keys
        "agent_id": AGENT_ID,
        "confidence": round(rng.uniform(0.85, 0.97), 2),
        "extraction_mode": "llm",
        "model_used": MODEL_USED,
        "prompt_hash": hashlib.sha256(
            f"seahorse-demo:{title}".encode()
        ).hexdigest(),
        "session_id": _agent_session(day),
        "source_type": "agent",
    }
    ep_id = _uuid7(when)
    valid_at = (
        dt.datetime.combine(valid_day, dt.time(0), tzinfo=UTC)
        if valid_day else None
    )
    _write_note(
        _memory_path(title, ep_id),
        ep_id=ep_id, created_at=when, provenance=provenance, body=body,
        valid_at=valid_at, cognitive_type=ctype, source_type="agent",
        title=title, summary=_derive_summary(body),
    )
    counts["agent"] += 1
    return ep_id


def observer_episode(source):
    """A hook-captured session turn (the worker's skip profile)."""
    day, prompt_number, prompt, events = source
    session_id = _uuid4()
    title = _build_title(prompt, session_id[:8], prompt_number)
    body = _render_turn_body(title, prompt, events)
    when = _clock(day)
    provenance = {  # alphabetical
        "agent_id": "unknown",
        "confidence": 1.0,
        "extraction_mode": "skip",
        "model_used": None,
        "prompt_hash": None,
        "session_id": session_id,
        "source_type": "agent",
    }
    ep_id = _uuid7(when)
    _write_note(
        _memory_path(title, ep_id),
        ep_id=ep_id, created_at=when, provenance=provenance, body=body,
        cognitive_type="episodic", source_type="agent",
        title=title, summary=_derive_summary(body),
    )
    counts["observer"] += 1
    return ep_id


def improve_successor(day, body, *, supersedes):
    """CLI `improve` correction (skip profile; no title/summary/ctype)."""
    when = _clock(day)
    provenance = {  # alphabetical
        "confidence": 1.0,
        "extraction_mode": "skip",
        "model_used": None,
        "prompt_hash": None,
        "source_type": "human",
    }
    ep_id = _uuid7(when)
    subject = next(  # the corrected H1 drives the new subject and filename
        line for line in body.splitlines() if line.lstrip().startswith("# ")
    ).lstrip()[2:].strip()
    valid_at = dt.datetime.combine(day, dt.time(0), tzinfo=UTC)
    _write_note(
        _memory_path(subject, ep_id),
        ep_id=ep_id, created_at=when, provenance=provenance, body=body,
        valid_at=valid_at, supersedes=supersedes,
        supersedes_reason="correction", source_type="human",
    )
    counts["improve"] += 1
    return when


def invalidate_root(path, *, ep_id, created_at, provenance, body, valid_at,
                    cognitive_type, source_type, title, summary,
                    invalid_at):
    """Rewrite a chain root post-improve: invalid_at appended after tags."""
    _write_note(
        path, ep_id=ep_id, created_at=created_at, provenance=provenance,
        body=body, valid_at=valid_at, invalid_at=invalid_at,
        invalid_at_last=True, cognitive_type=cognitive_type,
        source_type=source_type, title=title, summary=summary,
    )


def consolidated_note(cluster, *, representative_id):
    """`seahorse consolidate` output (skill LLM-synthesis profile)."""
    key, body = cluster["key"], cluster["body"]
    when = _clock(cluster["note_day"])
    provenance = {  # alphabetical
        "agent_id": "consolidator",
        "confidence": round(rng.uniform(0.87, 0.93), 2),
        "extraction_mode": "consolidated",
        "model_used": MODEL_USED,
        "prompt_hash": hashlib.sha256(
            f"seahorse-demo:{key}".encode()
        ).hexdigest(),
        "session_id": f"consolidate-{_uuid7(when)}",
        "source_type": "system",
    }
    ep_id = _uuid7(when)
    _write_note(
        _memory_path(key, ep_id),
        ep_id=ep_id, created_at=when, provenance=provenance, body=body,
        supersedes=representative_id, supersedes_reason="merge",
        cognitive_type="semantic", source_type="system",
        title=key, summary=_derive_summary(body),
    )
    counts["consolidated"] += 1


# -------------------------------------------------------------- episodes ---

for note in C.PERSONA:
    agent_episode(note["day"], note["title"], note["body"],
                  ctype=note["ctype"], valid_day=note["valid_at"])

for title, day, body in C.SOCIALS:
    agent_episode(day, title, body, ctype="social")

for title, day, body in C.PROJECT_INTROS:
    agent_episode(day, title, body, ctype="project_doc")

for title, day, body in C.DECISIONS:
    agent_episode(day, title, body, ctype="semantic", valid_day=day)

# Supersede chains: root (agent) -> CLI improve correction. The root file is
# rewritten afterwards with invalid_at appended (the invalidate-merge shape).
for root_title, root_day, root_body, _succ_title, succ_day, succ_body in C.CHAINS:
    when_root = _clock(root_day)
    root_prov = {
        "agent_id": AGENT_ID,
        "confidence": round(rng.uniform(0.85, 0.97), 2),
        "extraction_mode": "llm",
        "model_used": MODEL_USED,
        "prompt_hash": hashlib.sha256(
            f"seahorse-demo:{root_title}".encode()
        ).hexdigest(),
        "session_id": _agent_session(root_day),
        "source_type": "agent",
    }
    root_id = _uuid7(when_root)
    root_path = _memory_path(root_title, root_id)
    root_valid_at = dt.datetime.combine(root_day, dt.time(0), tzinfo=UTC)
    root_summary = _derive_summary(root_body)
    _write_note(
        root_path, ep_id=root_id, created_at=when_root,
        provenance=root_prov, body=root_body, valid_at=root_valid_at,
        cognitive_type="semantic", source_type="agent",
        title=root_title, summary=root_summary,
    )
    counts["agent"] += 1
    invalid_at = improve_successor(succ_day, succ_body, supersedes=root_id)
    invalidate_root(
        root_path, ep_id=root_id, created_at=when_root,
        provenance=root_prov, body=root_body, valid_at=root_valid_at,
        cognitive_type="semantic", source_type="agent",
        title=root_title, summary=root_summary, invalid_at=invalid_at,
    )

for title, day, body in C.STATUS:
    agent_episode(day, title, body, ctype="episodic", valid_day=day)

for title, day, body in C.SEMANTIC:
    agent_episode(day, title, body, ctype="semantic")

for title, day, body in C.RUNBOOKS:
    agent_episode(day, title, body, ctype="procedural")

# Observer turns + the consolidated note each cluster distills.
for cluster in C.CLUSTERS:
    source_ids = [observer_episode(s) for s in cluster["sources"]]
    consolidated_note(cluster, representative_id=source_ids[-1])

for title, day, body in C.PR_REVIEWS:
    agent_episode(day, title, body, ctype="episodic", valid_day=day)

for title, day, body in C.INCIDENT:
    agent_episode(day, title, body, ctype="episodic", valid_day=day)

for title, day, body in C.DEBUGGING:
    agent_episode(day, title, body, ctype="episodic", valid_day=day)

for title, day, body in C.MIGRATIONS:
    agent_episode(day, title, body, ctype="episodic", valid_day=day)

# ------------------------------------------------------------------ hubs ---

# Legacy mtimes: pinned-looking times (no microseconds), one per hub.
HUB_TIMES = ["10:15:30", "14:32:07", "09:20:44", "16:05:12", "11:48:03",
             "13:27:59", "15:10:26", "10:33:41", "12:19:08", "14:02:55",
             "09:56:33", "11:24:17", "15:41:29", "11:37:52", "16:22:48",
             "10:04:19"]
migration_session = _uuid7(MIGRATION_WHEN)
for (name, mtime_day, body), hhmmss in zip(C.HUBS, HUB_TIMES, strict=True):
    h, m, s = (int(x) for x in hhmmss.split(":"))
    mtime = dt.datetime.combine(mtime_day, dt.time(h, m, s), tzinfo=UTC)
    provenance = {  # literal insertion order — direct write, no DB round-trip
        "agent_id": MIGRATOR_AGENT_ID,
        "session_id": migration_session,
        "source_type": "human",
        "extraction_mode": "skip",
    }
    _write_note(
        HERE / f"{name}.md",
        ep_id=_uuid7(MIGRATION_WHEN), created_at=mtime,
        provenance=provenance, body=f"# {name}\n\n{body}\n",
        valid_at=mtime, cognitive_type="semantic",
    )
    counts["hubs"] += 1

# -------------------------------------------------------------- showcase ---

madrid_when = MADRID_WHEN + dt.timedelta(microseconds=rng.randrange(1, 1_000_000))
correction_at = CORRECTION_WHEN + dt.timedelta(
    microseconds=rng.randrange(1, 1_000_000))
madrid_id = _uuid7(madrid_when)
barcelona_id = _uuid7(correction_at)
madrid_prov = {
    "agent_id": AGENT_ID,
    "confidence": 0.92,
    "extraction_mode": "llm",
    "model_used": MODEL_USED,
    "prompt_hash": hashlib.sha256(
        b"seahorse-demo:Alex Vega lives in Madrid"
    ).hexdigest(),
    "session_id": _uuid4(),
    "source_type": "agent",
}

# Madrid: the agent-llm note, later corrected — invalid_at appended at the
# end of the frontmatter (the invalidate-merge artifact), body locked.
_write_note(
    HERE / C.SHOWCASE_MADRID["file"],
    ep_id=madrid_id, created_at=madrid_when, provenance=madrid_prov,
    body=C.SHOWCASE_MADRID["body"].replace("{barcelona_id}", barcelona_id),
    valid_at=dt.datetime.combine(C.SHOWCASE_MADRID["fact_day"],
                                 dt.time(0), tzinfo=UTC),
    invalid_at=correction_at, invalid_at_last=True,
    cognitive_type="social", source_type="agent",
    title=C.SHOWCASE_MADRID["title"], summary=C.SHOWCASE_MADRID["summary"],
)

# Barcelona: the CLI-improve successor (skip profile, no title/summary/ctype).
barcelona_prov = {
    "confidence": 1.0,
    "extraction_mode": "skip",
    "model_used": None,
    "prompt_hash": None,
    "source_type": "human",
}
_write_note(
    HERE / C.SHOWCASE_BARCELONA["file"],
    ep_id=barcelona_id, created_at=correction_at, provenance=barcelona_prov,
    body=C.SHOWCASE_BARCELONA["body"],
    valid_at=dt.datetime.combine(C.SHOWCASE_BARCELONA["fact_day"],
                                 dt.time(0), tzinfo=UTC),
    supersedes=madrid_id, supersedes_reason="correction",
    source_type="human",
)
counts["showcase"] += 2

# ------------------------------------------------------------------ README --

episodes_total = counts["agent"] + counts["observer"] + counts["improve"]
memory_total = episodes_total + counts["consolidated"]
grand_total = memory_total + counts["hubs"] + counts["showcase"]
readme = f"""# Demo vault (fictional)

A {grand_total}-note F3.1 demo vault, **entirely fictional** — the company
({C.ORG}), the people, the projects (Atlas, Beacon, Cinder, Drift, Ember,
Herald), and
every fact. Nothing here is real user memory. Safe for public screenshots and
docs.

Regenerate it:

```bash
python3 generate_demo_vault.py
```

The vault mirrors what Seahorse 1.0.0 actually writes into a real vault:

- `Memory/*.md` — {memory_total} notes: {episodes_total} episodes (the
  `seahorse materialize --mode all` view — agent memories, observer-captured
  session turns titled `... [session_tag:n]`, and CLI corrections) plus the
  {counts['consolidated']} dense notes `seahorse consolidate` distills from
  repeated session topics (each carries a `merge` supersession back to its
  most recent source; sources are left valid).
- Vault root — the human layer: {counts['hubs']} hub notes imported once by
  `seahorse frontmatter migrate` (their `created_at`/`valid_at` is the legacy
  file's mtime) and the hand-curated showcase pair below.
- Supersede chains: 7 corrections (`improve`) — 30k→40k msg/s, postcode→H3,
  90→180-day audit retention, hourly→15-minute refresh, 30→21-day hot
  retention, 02:00→03:30 UTC training window, Superset→static-exporter embed.
  The invalidated roots
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
"""
(HERE / "README.md").write_text(readme, encoding="utf-8")

# ------------------------------------------------------------- self-check --

assert episodes_total == 169, f"expected 169 episodes, got {episodes_total}"
assert counts["consolidated"] == 18, counts["consolidated"]
assert grand_total == 205, grand_total
n_memory = len(list(MEMORY.glob("*.md")))
assert n_memory == memory_total, (n_memory, memory_total)
print(f"wrote {episodes_total} episodes + {counts['consolidated']} consolidated"
      f" -> Memory/ ({n_memory} notes), {counts['hubs']} hubs +"
      f" {counts['showcase']} showcase at root ({grand_total} total)")
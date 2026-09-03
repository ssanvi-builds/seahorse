#!/usr/bin/env python3
"""Regenerate the fictional F3.1 demo vault.

Deterministic (fixed seed): same output on every run. Everything in here is
invented — no real person, company, or project. Safe for public screenshots.

Usage:  python3 generate_demo_vault.py
Writes one .md file per episode into this directory and rewrites README.md.
"""

from __future__ import annotations

import datetime as dt
import pathlib
import random
import re

HERE = pathlib.Path(__file__).resolve().parent
SEED = 20260903

PEOPLE = {
    "alex": "Alex Vega",        # the vault's user (fictional)
    "maya": "Maya Chen",
    "tomas": "Tomas Rivera",
    "priya": "Priya Nair",
    "daniel": "Daniel Okafor",
    "lena": "Lena Fischer",
    "sam": "Sam Whitaker",
}
PROJECTS = ["Atlas", "Beacon", "Cinder", "Drift", "Ember"]
ORG = "Northwind Analytics"

FIRST_DAY = dt.date(2026, 1, 12)
LAST_DAY = dt.date(2026, 9, 2)

random.seed(SEED)
_used_slugs: set[str] = set()
_counter = 0

# Hand-written showcase notes are never touched by the generator.
SHOWCASE = {"2026-05-10-persona-home-city.md", "2026-08-30-persona-home-city.md"}
for stale in HERE.glob("*.md"):
    if stale.name not in SHOWCASE:
        stale.unlink()


def _rand_ts(day: dt.date) -> str:
    t = dt.datetime.combine(day, dt.time(random.randint(8, 20), random.randint(0, 59)))
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _uuid7(day: dt.date) -> str:
    """Deterministic UUIDv7-shaped id (time-ordered, fictional)."""
    global _counter
    _counter += 1
    ms = int(dt.datetime.combine(day, dt.time(12)).timestamp() * 1000) + _counter
    tail = f"{random.getrandbits(96):024x}"
    return f"{ms:012x}-{random.getrandbits(16):04x}-7{tail[:3]}-8{tail[4:7]}-{tail[8:20]}"


def _slug(day: dt.date, title: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48]
    cand = f"{day.isoformat()}-{base}"
    n = 2
    while cand in _used_slugs:
        cand = f"{day.isoformat()}-{base}-{n}"
        n += 1
    _used_slugs.add(cand)
    return cand


def episode(day, title, *, ctype, tags, body, mode="llm", confidence=None,
            supersedes=None, reason=None, valid_at=None, invalid_at=None):
    slug = _slug(day, title)
    prov = {
        "agent_id": "seahorse/claude-code",
        "session_id": f"sess_01J{random.getrandbits(48):012X}",
        "source_type": "agent",
        "extraction_mode": mode,
    }
    if mode == "llm":
        prov["model_used"] = "claude-sonnet-5"
        prov["confidence"] = confidence if confidence is not None else round(random.uniform(0.85, 0.97), 2)
        prov["tool"] = "seahorse-mcp"
    fm = [
        "---",
        f"id: {_uuid7(day)}",
        f"created_at: {_rand_ts(day)}",
        "schema_version: 0.1.0",
        "provenance:",
        *[f"  {k}: {v}" for k, v in prov.items()],
        f"valid_at: {valid_at or day.isoformat() + 'T00:00:00Z'}",
    ]
    if invalid_at:
        fm.append(f"invalid_at: {invalid_at}")
    if supersedes:
        fm += [f"supersedes: {supersedes}", f"supersedes_reason: {reason}"]
    fm += [f"cognitive_type: {ctype}", f'title: "{title}"',
           f"summary: \"{title}. Fictional demo episode for the F3.1 format.\"",
           f"tags: [{', '.join(tags)}]", "---", "", body.strip(), ""]
    ep_id = fm[1].split("id: ")[1]
    (HERE / f"{slug}.md").write_text("\n".join(fm), encoding="utf-8")
    return {"id": ep_id, "slug": slug}


def days_spread(n: int) -> list[dt.date]:
    span = (LAST_DAY - FIRST_DAY).days
    return [FIRST_DAY + dt.timedelta(days=int(i * span / (n - 1))) for i in range(n)]


written: list[str] = []

# ---------------------------------------------------------------- persona --
day = dt.date(2026, 1, 12)
episode(day, "Alex Vega works as a data engineer", ctype="social",
        tags=["person", "work"],
        body=f"# Alex Vega works as a data engineer\n\n"
             f"Alex Vega is a data engineer at [[{ORG}]], working remotely.")

episode(dt.date(2026, 1, 20), "Alex Vega joined Northwind Analytics in 2024",
        ctype="social", tags=["person", "work"],
        body=f"# Alex Vega joined in 2024\n\n"
             f"Alex joined [[{ORG}]]'s data platform group in spring 2024, "
             f"on the same team as [[{PEOPLE['tomas']}]].")

# home-city chain root is the hand-written Madrid note; Barcelona note exists too.

for name, person in PEOPLE.items():
    if name == "alex":
        continue
    episode(FIRST_DAY + dt.timedelta(days=random.randint(5, 40)),
            f"{person} is on the data platform team", ctype="social",
            tags=["person", "team"],
            body=f"# {person} is on the data platform team\n\n"
                 f"[[{person}]] works with Alex at [[{ORG}]] on "
                 f"[[Atlas]] and [[Beacon]].")

colleagues = [p for p in PEOPLE.values() if p != "Alex Vega"]
episode(FIRST_DAY + dt.timedelta(days=30), "Maya Chen reviews Alex's pull requests",
        ctype="social", tags=["person", "workflow"],
        body="# Maya reviews Alex's PRs\n\n"
             f"[[{PEOPLE['maya']}]] is Alex's default reviewer for [[Atlas]] work.")

episode(FIRST_DAY + dt.timedelta(days=44), "Tomas Rivera runs the platform group",
        ctype="social", tags=["person", "team"],
        body="# Tomas runs the platform group\n\n"
             f"[[{PEOPLE['tomas']}]] manages the data platform group at [[{ORG}]]; "
             f"1:1s are on Thursdays.")

# ------------------------------------------------------------- projects ----
project_blurbs = {
    "Atlas": "the ingestion platform: Kafka pipelines into the lakehouse",
    "Beacon": "the alerting service on top of Atlas streams",
    "Cinder": "the nightly ML feature pipeline",
    "Drift": "the internal dashboards layer",
    "Ember": "the auth and access-control service",
}
for proj, blurb in project_blurbs.items():
    d = FIRST_DAY + dt.timedelta(days=random.randint(10, 40))
    episode(d, f"Project {proj}: what it is", ctype="project_doc",
            tags=[proj.lower(), "project"],
            body=f"# Project {proj}\n\n"
                 f"{blurb.capitalize()}. Owned by the data platform group at "
                 f"[[{ORG}]]. Key people: {colleagues[0]}, {colleagues[1]}.")

# ------------------------------------------------------------- decisions ---
decisions = [
    ("atlas", "Atlas uses Kafka exactly-once semantics", "we accept the throughput cost"),
    ("atlas", "Atlas retention set to 30 days", "storage budget signed off by Tomas"),
    ("beacon", "Beacon alerts route to PagerDuty", "Slack-only routing was too noisy"),
    ("beacon", "Beacon uses H3 cells for geo-alerts", "cleaner than postcode polygons"),
    ("cinder", "Cinder trains nightly at 02:00 UTC", "avoid the BI refresh window"),
    ("cinder", "Cinder feature store is Feast", "vs. a hand-rolled store"),
    ("drift", "Drift dashboards are dbt-generated", "one source of truth for metrics"),
    ("drift", "Drift embeds go through Superset", "not iframe-per-tool"),
    ("ember", "Ember issues short-lived tokens", "15-minute TTL, refresh flow"),
    ("ember", "Ember follows RFC 8629 for errors", "matches the platform error style"),
]
for proj, title, why in decisions:
    d = FIRST_DAY + dt.timedelta(days=random.randint(30, 150))
    p = proj.capitalize()
    episode(d, title, ctype="project_doc", tags=[proj, "decision"],
            body=f"# {title}\n\n**Why:** {why}. Discussed in the weekly sync "
                 f"of [[{p}]] with [[{colleagues[2]}]]. Affects [[{p}]] "
                 f"and downstream [[{colleagues[3]}]] dashboards.")

# superseded decision chains (2 per project pair)
supersede_chains = [
    ("Atlas", [("Atlas retention set to 30 days", "30 days filled the lakehouse budget"),
               ("Atlas retention set to 21 days", "budget review cut it back")]),
    ("Cinder", [("Cinder trains nightly at 02:00 UTC", "collided with EU batch window"),
                ("Cinder trains nightly at 03:30 UTC", "clean window found")]),
    ("Ember", [("Ember issues short-lived tokens", "15-minute TTL annoyed mobile clients"),
               ("Ember tokens move to 1-hour TTL", "mobile team escalation")]),
    ("Drift", [("Drift embeds go through Superset", "Superset license terms changed"),
               ("Drift embeds move to a static exporter", "rebuilds daily, no license risk")]),
]
for proj, chain in supersede_chains:
    d0 = FIRST_DAY + dt.timedelta(days=random.randint(40, 130))
    parent_id, parent_slug = None, None
    for i, (title, why) in enumerate(chain):
        d = d0 + dt.timedelta(days=30 * (i + 1))
        res = episode(d, title, ctype="project_doc", tags=["decision", "superseded" if i == 0 else "current"],
                      body=f"# {title}\n\n**Why:** {why}. Decided in the weekly "
                           f"sync of [[{proj}]]."
                           + (f"\n\nSupersedes the earlier decision on this subject."
                              if i else "\n\nLater revised — see the current decision."),
                      supersedes=parent_id, reason="correction")
        if parent_slug:  # make the chain visible in the Obsidian graph too
            path = HERE / f"{res['slug']}.md"
            path.write_text(path.read_text(encoding="utf-8")
                            + f"\nSupersedes [[{parent_slug}]].\n", encoding="utf-8")
        parent_id, parent_slug = res["id"], res["slug"]

# ----------------------------------------------------------- status/episodic
statuses = [
    ("Atlas", ["streaming ingest stable at 40k msg/s", "replay tool shipped",
               "lag under 2s p99"]),
    ("Beacon", ["geo-alerts in beta", "false-positive rate down 40%",
                "on-call handover done"]),
    ("Cinder", ["nightly trainings green for 30 days", "feature parity reached",
                "backfill complete"]),
    ("Drift", ["top 10 dashboards migrated", "row-level security live",
               "embeds deprecated"]),
    ("Ember", ["token TTL migration done", "audit log shipped", "pen test passed"]),
]
for proj, updates in statuses:
    for i, upd in enumerate(updates):
        d = FIRST_DAY + dt.timedelta(days=random.randint(60, 220))
        episode(d, f"{proj} status: {upd}", ctype="episodic", tags=[proj.lower(), "status"],
                body=f"# {proj} status update\n\n{upd.capitalize()}. Shared in the "
                     f"weekly sync of [[{proj}]] with [[{colleagues[1]}]] "
                     f"and [[{colleagues[4]}]].")

# ---------------------------------------------------------------- sessions -
meeting_titles = ["weekly sync", "design review", "incident retro", "planning session",
                  "architecture discussion", "onboarding pairing"]
for proj in PROJECTS:
    for _ in range(6):
        d = FIRST_DAY + dt.timedelta(days=random.randint(15, 230))
        title = f"{proj} {random.choice(meeting_titles)}"
        att = random.sample(colleagues, 3)
        episode(d, title, ctype="episodic", tags=[proj.lower(), "meeting"],
                body=f"# {title}\n\nAttendees: "
                     f"{', '.join(f'[[{a}]]' for a in att)}. Notes captured "
                     f"against [[{proj}]]. Follow-ups assigned to [[{att[0]}]].")

# ---------------------------------------------------------------- semantic -
semantics = [
    "Alex Vega prefers written async updates",
    "Alex Vega's editor is Neovim",
    "Alex Vega uses a dark theme everywhere",
    "Alex Vega runs Arch Linux on the work laptop",
    "Alex Vega reads papers on Friday mornings",
    "The platform org uses trunk-based development",
    "Production runs on a managed Kubernetes cluster",
    "The lakehouse is Iceberg on S3",
    "On-call rotation is weekly, starting Mondays",
    "Alex Vega drinks too much coffee during incident reviews",
]
for i, fact in enumerate(semantics):
    d = FIRST_DAY + dt.timedelta(days=random.randint(5, 200))
    anchor = "Alex Vega" if fact.startswith("Alex Vega") else ORG
    episode(d, fact, ctype="semantic", tags=["preference", "infra"],
            body=f"# {fact}\n\nCaptured from a work session at [[{anchor}]]. "
                 f"Fictional demo fact.")

# -------------------------------------------------------------- procedural -
episode(FIRST_DAY + dt.timedelta(days=60), "How to deploy Atlas safely",
        ctype="procedural", tags=["atlas", "runbook"],
        body="# Deploying Atlas\n\n1. Green build on main\n2. Canary 5% for 30 min\n"
             "3. Watch the [[Atlas]] lag panel\n4. Full rollout")
episode(FIRST_DAY + dt.timedelta(days=90), "How to take the on-call handover",
        ctype="procedural", tags=["runbook", "oncall"],
        body="# On-call handover\n\n1. Read the open incidents\n2. Check [[Beacon]] "
             "alert noise\n3. Ping [[%s]] with open questions" % colleagues[0])

# --------------------------------------------------------- stable-name hubs -
# Hub notes with stable filenames so the [[wiki-links]] in other bodies resolve
# in the Obsidian graph (dated episode filenames can't be link targets).
hubs: list[tuple[str, str, str, list[str]]] = [
    (f"{ORG}", "project_doc", f"{ORG}: the employer", ["org"]),
    ("Alex Vega", "social", "Alex Vega — the user of this vault", ["person"]),
]
hubs += [(p, "social", f"{p} — colleague on the data platform team",
          ["person", "team"]) for p in colleagues]
hubs += [(p, "project_doc", f"Project {p} — overview", [p.lower()])
         for p in PROJECTS]
for name, ctype, title, tags in hubs:
    d = FIRST_DAY + dt.timedelta(days=1)
    fm = [
        "---",
        f"id: {_uuid7(d)}",
        f"created_at: {_rand_ts(d)}",
        "schema_version: 0.1.0",
        "provenance:",
        "  agent_id: seahorse/claude-code",
        "  session_id: sess_01JGENESIS00000000",
        "  source_type: human",
        "  extraction_mode: skip",
        "valid_at: 2026-01-12T00:00:00Z",
        f"cognitive_type: {ctype}",
        f'title: "{title}"',
        f"summary: \"{title}. Fictional demo hub note (stable filename).\"",
        f"tags: [{', '.join(tags)}]",
        "---",
        "",
        f"# {name}",
        "",
        ("Hub note: every mention of "
         + (f"[[{ORG}]] or its projects lands here." if name == ORG
            else f"[[{name}]] across the vault points at this note.")
         + " Fictional."),
        "",
    ]
    (HERE / f"{name}.md").write_text("\n".join(fm), encoding="utf-8")

# ---------------------------------------------------------------- write out -
count = len([p for p in HERE.glob("*.md") if p.name != "README.md"])
readme = f"""# Demo vault (fictional)

A {count}-episode F3.1 demo vault, **entirely fictional** — the company
({ORG}), the people, the projects ({', '.join(PROJECTS)}), and every fact.
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
"""
(HERE / "README.md").write_text(readme, encoding="utf-8")
print(f"wrote {count} episode notes + README")
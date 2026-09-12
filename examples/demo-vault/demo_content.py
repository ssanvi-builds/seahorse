"""Fictional content for the F3.1 demo vault — pure data, no writers.

Everything here is invented: the company (Northwind Analytics), the people
(Alex Vega and colleagues), the projects (Atlas, Beacon, Cinder, Drift, Ember),
every prompt, path, and id fragment. No real person, session, or repository.
Safe for public screenshots.

The structure mirrors what the real write paths produce (all verified against
the shipped 1.0.0 CLI, the migrator, and the real serializer):

- agent episodes    MCP ``remember`` with LLM extraction — clean titles.
- observer episodes session turns captured by the hooks — the title is the
                    first line of the user prompt plus ``[{tag}:{n}]``; the
                    stripped first line is the clustering key, so the three
                    occurrences of an opener under each CLUSTER topic are
                    exactly what ``seahorse consolidate`` distills. Every
                    prompt ends at a sentence boundary so the deterministic
                    summary (first sentence after the H1) cuts cleanly.
- chains            ``improve`` successions — the root is an agent episode
                    later corrected from the CLI by Alex (skip-path edit,
                    confidence 1.0); the root keeps its body and gains
                    ``invalid_at`` (appended at the end of the frontmatter,
                    where the invalidate-merge puts it); the successor is a
                    fresh note whose body opens with the corrected H1.
- consolidated      ``seahorse consolidate`` output — ``merge`` supersession
                    of the most recent source, LLM-synthesis provenance, no
                    ``valid_at``, sources left valid.
- hubs              Alex's pre-seahorse notes, migrated by ``seahorse
                    frontmatter migrate`` — ``valid_at == created_at ==`` file
                    mtime, ``cognitive_type: semantic``.
- showcase          the hand-curated home-city pair at the vault root; bodies
                    are locked (README snippet + demo clip).

Chronology is internally consistent: notes created early in 2026 never state
facts that only became true later — the chains and consolidated notes record
how those facts changed.
"""

from __future__ import annotations

import datetime as dt

D = dt.date  # alias for compact date literals

ORG = "Northwind Analytics"

PEOPLE = {
    "alex": "Alex Vega",        # the vault's user (fictional)
    "maya": "Maya Chen",
    "tomas": "Tomas Rivera",
    "priya": "Priya Nair",
    "daniel": "Daniel Okafor",
    "lena": "Lena Fischer",
    "sam": "Sam Whitaker",
}

# ----------------------------------------------------------------- persona --

PERSONA = [
    {
        "title": "Alex Vega works as a data engineer",
        "day": D(2026, 1, 12),
        "ctype": "social",
        "valid_at": D(2026, 1, 12),
        # LOCKED: quoted verbatim in the project README snippet.
        "body": "# Alex Vega works as a data engineer\n\n"
                "Alex Vega is a data engineer at [[Northwind Analytics]], "
                "working remotely.",
    },
    {
        "title": "Alex Vega joined Northwind Analytics in 2024",
        "day": D(2026, 1, 20),
        "ctype": "social",
        "valid_at": D(2024, 3, 15),
        # LOCKED: the demo clip (examples/demo-clip/demo.tape) remembers this fact.
        "body": "# Alex Vega joined in 2024\n\n"
                "Alex joined [[Northwind Analytics]]'s data platform group in "
                "spring 2024, on the same team as [[Tomas Rivera]].",
    },
]

# ----------------------------------------------------------------- socials --
# Standing facts about people — no valid_at (no time anchor for the LLM to
# extract); recorded as true when the agent learned them.

SOCIALS = [
    ("Maya Chen is on the data platform team", D(2026, 1, 17),
     "Maya works on [[Beacon]] alerting and is the second reviewer for "
     "[[Atlas]] changes. She blocks merges that ship without tests."),
    ("Tomas Rivera is on the data platform team", D(2026, 1, 24),
     "[[Tomas Rivera]] manages the data platform group at "
     "[[Northwind Analytics]]. 1:1s are on Thursdays."),
    ("Priya Nair is on the data platform team", D(2026, 1, 29),
     "[[Priya Nair]] owns [[Cinder]], the feature pipeline, and its training "
     "schedules. Escalations go to her before the on-call rota."),
    ("Daniel Okafor is on the data platform team", D(2026, 2, 3),
     "[[Daniel Okafor]] owns the [[Atlas]] consumer group and the lag "
     "dashboards. He wrote the replay tool."),
    ("Lena Fischer is on the data platform team", D(2026, 2, 7),
     "[[Lena Fischer]] owns [[Drift]] and the row-level security policy on "
     "the lakehouse tables."),
    ("Sam Whitaker is on the data platform team", D(2026, 2, 12),
     "[[Sam Whitaker]] owns [[Ember]] and reviews every auth-related change "
     "with the security checklist."),
    ("Maya Chen reviews Alex's pull requests", D(2026, 2, 20),
     "[[Maya Chen]] is Alex's default reviewer for [[Atlas]] and [[Beacon]] "
     "work. Her standing rule: no merge without a test that would have caught "
     "the regression."),
    ("Iris Kovac is on the data platform team", D(2026, 2, 16),
     "[[Iris Kovac]] runs the on-call rota and the incident review calendar. "
     "Escalations that stall with the rota reach [[Tomas Rivera]]."),
    ("Noah Bennett is on the data platform team", D(2026, 2, 21),
     "[[Noah Bennett]] leads the mobile clients that consume the data API "
     "and [[Ember]] tokens; his battery-drain report opened the token "
     "lifecycle review."),
    ("Tomas Rivera runs the platform group", D(2026, 3, 2),
     "[[Tomas Rivera]] runs the weekly platform sync and owns the storage "
     "budget sign-offs, including the [[Atlas]] retention reviews."),
    ("Iris Kovac runs the on-call rota", D(2026, 3, 9),
     "Handovers land Mondays, [[Iris Kovac]] assigns the weekly slots, and "
     "[[Beacon]] acks sync to her rota so open sevs survive the swap."),
    ("Noah Bennett owns the mobile clients", D(2026, 3, 16),
     "[[Noah Bennett]] owns the mobile SDKs against the data API; the "
     "refresh traffic from his clients is what [[Ember]]'s token lifecycle "
     "review retuned."),
]

# ---------------------------------------------------------- project intros --
# Written when the agent first mapped the projects — no facts from later
# than the note's day (the chains and consolidated notes record what
# changed since).

PROJECT_INTROS = [
    ("Project Atlas: what it is", D(2026, 1, 22),
     "Atlas is the ingestion platform: Kafka topics land as Iceberg tables on "
     "S3. Exactly-once semantics are under evaluation; ingest sustains 30k "
     "msg/s today. The consumer group is owned by [[Daniel Okafor]]."),
    ("Project Beacon: what it is", D(2026, 1, 27),
     "Beacon is the alerting service on top of Atlas streams. Every alert "
     "lands in the platform Slack channel today; a severity-split routing "
     "review is coming. [[Maya Chen]] owns it."),
    ("Project Cinder: what it is", D(2026, 2, 2),
     "Cinder is the nightly ML feature pipeline: Feast on the lakehouse with "
     "dbt-managed transformations, training at 02:00 UTC. [[Priya Nair]] "
     "owns it."),
    ("Project Drift: what it is", D(2026, 2, 6),
     "Drift is the internal dashboards layer over the lakehouse: dbt-defined "
     "metrics with row-level security, embedded through Superset with hourly "
     "rebuilds. [[Lena Fischer]] owns it."),
    ("Project Ember: what it is", D(2026, 2, 10),
     "Ember is the auth and access-control service: OAuth2 with OIDC for the "
     "data API, short-lived access tokens (15 minutes today; a lifecycle "
     "review is open). [[Sam Whitaker]] owns it."),
    ("Project Herald: what it is", D(2026, 2, 17),
     "Herald is the partner-facing data portal: read-only dashboards served "
     "from the [[Drift]] static exporter and authenticated with [[Ember]] "
     "service tokens. [[Lena Fischer]] and [[Sam Whitaker]] own it together."),
]

# -------------------------------------------------------------- decisions --
# Agent-recorded decisions. valid_at = the decision day (the LLM extracted
# "decided today" as a date).

DECISIONS = [
    ("Atlas uses Kafka exactly-once semantics", D(2026, 2, 24),
     "We accept the throughput cost: duplicate orders events are worse than "
     "a slower ingest. The orders topic relies on it for dedup."),
    ("Cinder feature store is Feast", D(2026, 3, 10),
     "Chose Feast over a hand-rolled store: registry, point-in-time "
     "correctness, and backfill tooling come for free."),
    ("Ember follows OAuth2 with OIDC", D(2026, 3, 19),
     "The data API authenticates via OAuth2 with OIDC; service accounts use "
     "the client-credentials flow. No custom token formats."),
    ("On-call escalations page the rota before Priya", D(2026, 3, 31),
     "Page [[Iris Kovac]]'s rota first for anything consumer-facing; "
     "[[Priya Nair]] is escalated only when the rota cannot triage within "
     "15 minutes."),
    ("The archive tier uses quarterly Glacier vaults", D(2026, 4, 8),
     "Archived [[Atlas]] partitions move to Glacier-class storage, one "
     "vault per quarter; restores are documented in the replay runbook."),
    ("H3 resolution 7 is the geo-alert default", D(2026, 6, 8),
     "Resolution 7 balances edge blur against cell count for [[Beacon]] "
     "geo-alerts; resolution 9 is reserved for the dense urban pilots."),
    ("Partner portal traffic serves from the exporter CDN", D(2026, 7, 2),
     "[[Herald]] serves exporter-built assets only — no live queries reach "
     "the portal tier, so [[Drift]]'s row-level security holds at the edge."),
    ("Beacon alert acks sync to the on-call rota", D(2026, 5, 14),
     "Acknowledging a [[Beacon]] alert updates the on-call rota so handovers "
     "never drop an open sev."),
    ("The data API emits OpenAPI specs from the service repo", D(2026, 6, 11),
     "The versioned data API generates its OpenAPI spec in CI — hand-written "
     "specs drift and are forbidden."),
    ("Atlas consumers use static group membership", D(2026, 7, 21),
     "After the consumer-lag incident, consumers register with static "
     "membership so deploys no longer trigger rebalance storms."),
]

# ------------------------------------------------------------ supersede chains
# (root_title, root_day, root_body, succ_title, succ_day, succ_body)
# Roots are agent episodes; successors are Alex's CLI `improve` corrections,
# whose body opens with the corrected H1 (so the successor gets a clean new
# subject and filename).

CHAINS = [
    ("Atlas ingestion baseline is 30k msg/s", D(2026, 2, 18),
     "Sustained ingest through the exactly-once evaluation: 30k msg/s on "
     "the orders and events topics, p99 lag stable at 1.4s.",
     "Atlas ingestion baseline is 40k msg/s", D(2026, 6, 9),
     "# Atlas ingestion baseline is 40k msg/s\n\n"
     "The exactly-once tuning plus the partition re-balance raised the "
     "sustained baseline from 30k to 40k msg/s; p99 lag stays under 2s."),
    ("Beacon geo-alerts use postcode polygons", D(2026, 1, 29),
     "Geo-alerts match postcodes to polygons today; border alerts blur at "
     "polygon edges and the cell-size debate is open.",
     "Beacon geo-alerts use H3 cells", D(2026, 4, 14),
     "# Beacon geo-alerts use H3 cells\n\n"
     "H3 cells replaced postcode polygons — uniform resolution across "
     "regions, border alerts stopped blurring."),
    ("Ember audit log keeps 90 days", D(2026, 3, 3),
     "The audit log retention default is 90 days, matching the original "
     "compliance sign-off.",
     "Ember audit log keeps 180 days", D(2026, 5, 26),
     "# Ember audit log keeps 180 days\n\n"
     "The compliance review doubled the retention window to 180 days; the "
     "backfill of older entries completed the same week."),
    ("Drift dashboards refresh hourly", D(2026, 2, 5),
     "Dashboards rebuild hourly — the full rebuild is the only refresh path "
     "today.",
     "Drift dashboards refresh every 15 minutes", D(2026, 6, 30),
     "# Drift dashboards refresh every 15 minutes\n\n"
     "The static exporter made 15-minute refreshes cheaper than the hourly "
     "full rebuild; the cadence moved with the partner-portal cutover."),
    ("Atlas hot retention is 30 days", D(2026, 2, 10),
     "Flat 30-day retention on every Atlas topic, matching the original "
     "capacity plan.",
     "Atlas hot retention is 21 days", D(2026, 3, 5),
     "# Atlas hot retention is 21 days\n\n"
     "The budget review cut hot retention from 30 to 21 days on all topics; "
     "older partitions move to the quarterly archive tier."),
    ("Cinder trains at 02:00 UTC", D(2026, 2, 2),
     "The nightly Cinder training window opens at 02:00 UTC, right after "
     "the ingest lull.",
     "Cinder trains at 03:30 UTC", D(2026, 5, 19),
     "# Cinder trains at 03:30 UTC\n\n"
     "The training window moved from 02:00 to 03:30 UTC to clear the EU "
     "batch window; feature freshness for the EU-morning dashboards "
     "recovered."),
    ("Drift partner portal embeds through Superset", D(2026, 2, 6),
     "Partner-facing dashboards embed through the Superset embedded path, "
     "the same one the internal tools use.",
     "Drift partner portal embeds via the static exporter", D(2026, 6, 16),
     "# Drift partner portal embeds via the static exporter\n\n"
     "The partner portal cut over to the static exporter — the Superset "
     "embed path is retired for external audiences."),
]

# ------------------------------------------------------------------ status --
# (title, day, body) — episodic state snapshots; valid_at = the day.

STATUS = [
    ("Atlas status: consumer lag under 2s p99", D(2026, 8, 4),
     "Post-incident check across the last deploys: p99 consumer lag sits at "
     "1.1s with zero rebalances since static membership landed. The lag "
     "panel is the deploy gate now."),
    ("Atlas status: replay tool shipped", D(2026, 4, 2),
     "The [[Atlas]] replay tool landed: dry-run reports, dedup checks, and "
     "archive-offset support coming with the retention change. Backfills no "
     "longer need hand-run scripts."),
    ("Atlas status: orders topic dedup verified end to end", D(2026, 6, 17),
     "Verified the orders topic dedup end to end after the exactly-once "
     "tuning: a forced duplicate window is rejected at ingest, row counts "
     "match Iceberg."),
    ("Beacon status: false-positive rate down 40%", D(2026, 5, 6),
     "First week on the severity-split routing: total alerts down from 812 "
     "to 487 weekly, acks within 5 minutes up from 41% to 83%."),
    ("Beacon status: geo-alerts in beta", D(2026, 3, 12),
     "Geo-alerts entered beta on the H3 cells pilot — the migration from "
     "postcode polygons is underway, and border alerts stopped blurring in "
     "the pilot regions."),
    ("Cinder status: nightly trainings green for 30 days", D(2026, 7, 8),
     "Thirty consecutive green trainings since the 03:30 UTC window settled "
     "— the EU batch collision is fully behind us."),
    ("Cinder status: backfill complete for Q1 features", D(2026, 5, 21),
     "Q1 features backfilled through Feast: point-in-time joins validated "
     "against the training cutoffs."),
    ("Drift status: row-level security live", D(2026, 6, 3),
     "Row-level security is enforced at export time since PR 93 merged — "
     "ahead of the partner-portal cutover, so the policy lands before the "
     "traffic does."),
    ("Ember status: token TTL migration done", D(2026, 4, 29),
     "Every client is on the 1-hour access tokens with rotation; the "
     "15-minute TTL path is deleted."),
    ("Ember status: audit log shipped", D(2026, 3, 27),
     "The audit log writer shipped with the 90-day retention default; "
     "rotation entries appear on every refresh."),
    ("On-call rotation v2 is live", D(2026, 5, 18),
     "The rota swap landed: handovers inherit open sevs through the ack "
     "sync, and [[Iris Kovac]]'s calendar is the source of truth for the "
     "slots."),
    ("Kafka broker upgrade complete", D(2026, 7, 6),
     "All brokers on 3.8 after two weekend windows; consumer groups "
     "unchanged and p99 lag flat throughout. The tiered storage flag stays "
     "off."),
    ("Partner portal launched on the exporter", D(2026, 7, 9),
     "[[Herald]] opened to the first partner cohort serving exporter-built "
     "assets only: zero live queries, zero CSP surprises."),
    ("Beacon geo-alerts fully migrated to H3", D(2026, 6, 12),
     "The last postcode-polygon region moved to H3 cells; border-alert blur "
     "reports stopped the same week."),
    ("Cinder backfill tooling in production", D(2026, 6, 1),
     "The Feast backfill job from PR 267 runs point-in-time-correct "
     "backfills on demand; the Q1 feature set was its first customer."),
    ("OpenAPI SDKs shipped to client teams", D(2026, 8, 28),
     "Generated SDKs went out to both client teams with the sunset-date "
     "tracker wired to their dashboards; hand-written clients are retired."),
]

# ---------------------------------------------------------------- semantic --
# Standing knowledge and preferences — no valid_at (no time anchor).

SEMANTIC = [
    ("The lakehouse is Iceberg on S3", D(2026, 2, 14),
     "All [[Atlas]] tables land as Iceberg on S3 — the lakehouse contract "
     "every project builds on."),
    ("Production runs on a managed Kubernetes cluster", D(2026, 2, 25),
     "Production workloads run on the managed Kubernetes cluster; deploys "
     "are manifests, never snowflakes."),
    ("The platform org uses trunk-based development", D(2026, 3, 5),
     "Short-lived branches into main, reviewed within a day — [[Maya Chen]]'s "
     "review rule is the real gate."),
    ("On-call rotation is weekly, starting Mondays", D(2026, 3, 24),
     "The on-call handover runs Mondays; [[Beacon]] acks sync to the rota so "
     "open sevs survive handovers."),
    ("Alex Vega's editor is Neovim", D(2026, 1, 15),
     "Neovim with a minimal plugin set — the same config on both machines."),
    ("Alex Vega prefers written async updates", D(2026, 1, 30),
     "Written updates in the channel over sync pings; if it isn't written "
     "down, it didn't happen."),
    ("Alex Vega uses a dark theme everywhere", D(2026, 2, 9),
     "Dark theme in the editor, terminal, and Obsidian — screenshots come "
     "out consistent."),
    ("Alex Vega drinks too much coffee during incident reviews", D(2026, 7, 16),
     "The counter goes up during incident reviews — the retro noted it "
     "twice. Hydration follow-up pending."),
    ("sev-2 means a 15-minute response", D(2026, 4, 21),
     "Severity definitions: sev-1 pages within 5 minutes, sev-2 within 15, "
     "sev-3 within the working day — [[Beacon]]'s routing maps to these."),
    ("Geo-alerts match at H3 resolution 7", D(2026, 6, 9),
     "Resolution 7 cells are the [[Beacon]] geo-alert default; the pilot "
     "regions on resolution 9 are exceptions, not the rule."),
    ("The archive tier is read-through", D(2026, 4, 15),
     "Archived partitions are read in place — nothing is staged back to hot "
     "before a replay; the runbook documents the latency difference."),
    ("dbt models split staging from marts", D(2026, 5, 18),
     "Staging models clean, marts publish; the naming linter enforces the "
     "prefix rule across the [[Drift]] and [[Cinder]] repos."),
    ("Client SDKs are generated, never hand-written", D(2026, 8, 10),
     "The data API's clients build from the CI-generated OpenAPI spec; a "
     "hand-written client fails review on sight."),
    ("The partner portal is read-only", D(2026, 6, 18),
     "[[Herald]] serves dashboards and never writes — every portal route "
     "is a GET against exporter assets or the versioned data API."),
]

# -------------------------------------------------------------- procedural --
# A lead prose sentence after the H1 — the deterministic summary takes it
# (a body starting with a numbered list would summarize as "Label:\n\n1.").

RUNBOOKS = [
    ("How to deploy Atlas safely", D(2026, 4, 7),
     "Canary-first deploy with the lag panel as the gate.\n\n"
     "1. Green build on main, changelog entry present\n"
     "2. Canary 5% of consumers for 30 minutes\n"
     "3. Watch the [[Atlas]] lag panel — p99 under 2s or roll back\n"
     "4. Full rollout, then post the deploy note in the weekly sync"),
    ("How to take the on-call handover", D(2026, 4, 16),
     "Read the open incidents before anything else.\n\n"
     "1. Read the open incidents in Beacon\n"
     "2. Check [[Beacon]] alert noise from the last rotation\n"
     "3. Ping [[Maya Chen]] with open questions before Friday"),
    ("How to roll back an Atlas deployment", D(2026, 7, 23),
     "Roll back to the previous image tag — never rebuild under pressure.\n\n"
     "1. Keep the previous image tag in the deploy manifest\n"
     "2. `atlas-deploy rollback --to <tag>` (bakes the old image, no rebuild)\n"
     "3. Watch the [[Atlas]] lag panel for one partition cycle\n"
     "4. Re-open the deploy ticket with the observed reason"),
    ("How to replay an Atlas topic into Iceberg", D(2026, 5, 12),
     "Dry-run first; the dedup report is the contract.\n\n"
     "1. `atlas-replay --topic orders --from <offset> --dry-run`\n"
     "2. Check the dedup report the tool prints (exactly-once does the rest)\n"
     "3. Run without `--dry-run`, then compare row counts in Iceberg"),
    ("How to backfill Cinder features", D(2026, 5, 14),
     "Point-in-time correctness is the contract; the backfill job enforces "
     "it.\n\n"
     "1. Identify the feature view and the entity window in [[Cinder]]\n"
     "2. `cinder-backfill --view <view> --from <date> --dry-run`\n"
     "3. Check the point-in-time join report against the training cutoffs\n"
     "4. Run for real, then verify row counts in the feature registry"),
    ("How to swap into the on-call rota mid-week", D(2026, 5, 30),
     "The swap must land in the rota before the swap happens, not after.\n\n"
     "1. Propose the swap in the channel and find a willing counterparty\n"
     "2. [[Iris Kovac]] updates the rota — the ack sync follows her calendar\n"
     "3. Hand over open sevs in writing; unwritten sevs are lost sevs\n"
     "4. Confirm the swap shows in [[Beacon]]'s rota view before logging off"),
    ("How to restore an archived Atlas partition", D(2026, 6, 20),
     "Read-through restore; the offsets come from the archive manifest.\n\n"
     "1. Find the quarter vault and offsets in the archive manifest\n"
     "2. `atlas-replay --topic <t> --from archive:<offset>` (no staging step)\n"
     "3. Expect slower reads — schedule outside the peak ingest windows\n"
     "4. Reconcile row counts in Iceberg before declaring the restore done"),
    ("How to regenerate a client SDK", D(2026, 8, 14),
     "Generated only; the spec in CI is the single source.\n\n"
     "1. Confirm the spec version on the data API release page\n"
     "2. `make sdk VERSION=<v>` — the generator reads the CI spec, not a copy\n"
     "3. Diff the public surface; any manual edit you find is a bug\n"
     "4. Ship to client teams with the sunset date of the version it "
     "replaces"),
]

# ----------------------------------------------------------------- clusters --
# Each topic: an identical session opener typed across sessions (that is
# the clustering key), three captured turns, and the note consolidate
# distills from them. `note_day` is after the last source.
# source = (day, prompt_number, prompt, [(tool_name, tool_input, tool_response)])
# The prompt's first line is the opener/title; every prompt ends at a
# sentence boundary so the deterministic summary cuts cleanly at the first
# sentence instead of truncating mid-JSON.

CLUSTERS = [
    {
        "key": "atlas retention policy discussion",
        "sources": [
            (D(2026, 2, 10), 7,
             "atlas retention policy discussion\n"
             "reopening this: 30 days flat is filling the lakehouse budget, "
             "we need options on the table before Tomas signs the next "
             "quarter.",
             [("Read", '{"file_path": "repos/atlas/config/retention.yaml"}',
               "retention: 30d, topics: 14, size_class: hot")]),
            (D(2026, 3, 5), 3,
             "atlas retention policy discussion\n"
             "21 days hot won the budget review, so now what happens to the "
             "older partitions.",
             [("Bash", '{"command": "atlas-admin retention --describe"}',
               "hot=30d cold=0d archive=disabled")]),
            (D(2026, 3, 24), 12,
             "atlas retention policy discussion\n"
             "final shape: 21 days hot plus a quarterly archive tier, and "
             "the replay tool needs the archive offsets documented.",
             [("Read", '{"file_path": "repos/atlas/config/retention.yaml"}',
               "retention: 21d, archive: quarterly"),
              ("Bash", '{"command": "atlas-replay --help"}',
               "--from accepts hot or archive offsets")]),
        ],
        "note_day": D(2026, 4, 8),
        "body": """# atlas retention policy discussion

[[Atlas]] keeps 21 days of hot Kafka data and moves older partitions to a quarterly archive tier.

## Context

The original 30-day flat retention filled the lakehouse budget two quarters
running, and the budget review made it clear the next sign-off
([[Tomas Rivera]]) would
not fund the same shape for Q3. The discussion ran across three sessions:
the storage review that opened it, the budget decision that cut hot storage
to 21 days, and the wrap-up that had to answer what happens to the older
partitions.

## Options considered

- **Keep 30 days flat.** Rejected: the storage trend line does not bend, and
  no one could name a consumer that actually reads past three weeks.
- **Cut to 21 days hot, drop the rest.** Rejected by [[Daniel Okafor]]: the
  replay tool needs older offsets for backfill windows, and the orders
  dedup checks replay from the archive.
- **21 days hot + quarterly archive tier.** Keeps every consumer's working
  set on hot storage while backfills and audits read the archive tier at
  access-latency cost.

## Decision

21 days hot retention on all Atlas topics; partitions older than 21 days
move to the quarterly archive tier. The replay tool accepts archive offsets
(`atlas-replay --from` documents both forms).

## Consequences

- Storage spend on the hot tier drops ~38% against the flat-30 shape.
- Backfills that need data older than 21 days read the archive tier —
  slower, so runbooks say to prefer hot offsets when they exist.
- The orders topic is unaffected on the hot path (dedup reads stay in-window).
- `retention.yaml` is the single source of truth; the runbook links to it.
""",
    },
    {
        "key": "ember token lifecycle review",
        "sources": [
            (D(2026, 1, 27), 5,
             "ember token lifecycle review\n"
             "mobile clients are refreshing every 12 minutes against the "
             "15-minute access token TTL, and the battery reports are "
             "flooding support.",
             [("Read", '{"file_path": "repos/ember/config/tokens.yaml"}',
               "access_ttl: 15m, rotation: disabled")]),
            (D(2026, 2, 19), 9,
             "ember token lifecycle review\n"
             "escalation from the mobile team landed, so the proposal on the "
             "table is one hour access tokens with rotation.",
             [("Bash", '{"command": "ember-audit token-refresh --rate"}',
               "p50 refresh interval: 12m4s, clients: 3400")]),
            (D(2026, 3, 12), 2,
             "ember token lifecycle review\n"
             "security review cleared the 1-hour TTL with rotation and "
             "refresh tokens, and Sam is documenting the rotation audit "
             "entries.",
             [("Read", '{"file_path": "repos/ember/config/tokens.yaml"}',
               "access_ttl: 1h, rotation: enabled, refresh: true")]),
        ],
        "note_day": D(2026, 3, 26),
        "body": """# ember token lifecycle review

Ember issues 1-hour access tokens with rotation and refresh tokens; the 15-minute TTL is retired.

## Context

The original 15-minute access token TTL forced the mobile clients into a
refresh every ~12 minutes (p50 from the audit data), and battery-drain
reports reached support faster than the tokens expired. [[Noah Bennett]]'s
mobile team escalated, and the review ran across three sessions: the
problem report, the proposal, and the security clearance.

## Options considered

- **Keep 15 minutes, tune clients.** Rejected: the refresh traffic is
  inherent to the TTL, not a client bug, and the escalation had executive
  attention.
- **1-hour TTL, no rotation.** Rejected by the security review: a leaked
  token would stay valid an hour with no revocation signal.
- **1-hour TTL + rotation + refresh tokens.** Rotation emits an audit entry
  per rotation, which gives the revocation signal the review wanted;
  [[Sam Whitaker]] documented the rotation audit format the same week.

## Decision

Access tokens live 1 hour, with rotation enabled and refresh tokens issued
to clients that need silent renewal. Every rotation writes an audit entry
the Ember audit log keeps (180-day retention since 2026-05-26).

## Consequences

- Refresh traffic drops from every ~12 minutes to the hourly boundary —
  the battery reports stop.
- A leaked access token is valid up to an hour, but the rotation audit
  entries give the security team an anomaly signal they did not have before.
- The TTL migration shipped 2026-04-29; the config lives in
  `repos/ember/config/tokens.yaml`.
""",
    },
    {
        "key": "beacon alert routing review",
        "sources": [
            (D(2026, 2, 3), 4,
             "beacon alert routing review\n"
             "Slack-only routing is too noisy and pages are getting lost in "
             "the channel, so we need a severity split before the next "
             "on-call cycle.",
             [("Bash", '{"command": "beacon-stats alerts --last 7d"}',
               "total: 812, acked_within_5m: 41%")]),
            (D(2026, 3, 17), 6,
             "beacon alert routing review\n"
             "proposal: sev-1 and sev-2 go to PagerDuty while sev-3 stays in "
             "Slack, with acks syncing to the rota.",
             [("Read", '{"file_path": "repos/beacon/config/routing.yaml"}',
               "routes: [slack:all], ack_sync: false")]),
            (D(2026, 4, 7), 11,
             "beacon alert routing review\n"
             "tuning done: sev definitions live in the config, ack sync is "
             "live, and noise is down 40% in the first week.",
             [("Bash", '{"command": "beacon-stats alerts --last 7d"}',
               "total: 487, acked_within_5m: 83%")]),
        ],
        "note_day": D(2026, 4, 21),
        "body": """# beacon alert routing review

Beacon routes sev-1 and sev-2 to PagerDuty and sev-3 to Slack, with alert
acks synced to the on-call rota.

## Context

Slack-only routing on [[Beacon]] meant every alert landed in the same
channel: 812 alerts
in one week, 41% acknowledged within five minutes, and the sev pages that
mattered were getting scrolled past. The review ran across three sessions:
the noise report, the routing proposal, and the tuning pass after the first
week.

## Options considered

- **Everything to PagerDuty.** Rejected: the false-positive rate would have
  burned the rota ([[Iris Kovac]]'s words) within a sprint.
- **Keep Slack, add keyword filters.** Rejected: filtering is invisible to
  the on-call and drifts; the routing decision belongs in config.
- **Split by severity, acks sync to the rota.** PagerDuty carries what
  pages, Slack carries what informs, and handovers stop dropping sevs.

## Decision

sev-1 and sev-2 route to PagerDuty; sev-3 stays in Slack. Severity
definitions live in `repos/beacon/config/routing.yaml`. Acknowledging an
alert updates the on-call rota (the decision recorded 2026-05-14).

## Consequences

- First-week numbers: total alerts 812 → 487 (noise −40%), acked-within-5m
  41% → 83%.
- sev-3 alerts in Slack carry an explicit expectation: acknowledged within
  the working day, not the 5-minute page window.
- The rota sync means handovers inherit open sevs — no "lost page" retro
  since it shipped.
""",
    },
    {
        "key": "cinder training window",
        "sources": [
            (D(2026, 4, 9), 8,
             "cinder training window\n"
             "the 02:00 UTC training collides with the EU batch window, and "
             "feature freshness for the EU-morning dashboards is degrading.",
             [("Bash", '{"command": "cinder-schedule describe"}',
               "train: 02:00 UTC, eu_batch: 02:00 UTC")]),
            (D(2026, 4, 28), 3,
             "cinder training window\n"
             "candidates on the table are 03:00 or 03:30 UTC, and I need the "
             "EU batch end times before picking.",
             [("Bash", '{"command": "batch-planner eu --window"}',
               "eu_batch: 02:00-03:10 UTC")]),
            (D(2026, 5, 19), 7,
             "cinder training window\n"
             "settled on 03:30 UTC so the EU batch is untouched and the "
             "dashboards pick fresh features by EU morning.",
             [("Read", '{"file_path": "repos/cinder/config/schedule.yaml"}',
               "train: 03:30 UTC")]),
        ],
        "note_day": D(2026, 6, 2),
        "body": """# cinder training window

Cinder trains nightly at 03:30 UTC, clear of the EU batch window it used to collide with.

## Context

The original 02:00 UTC training window collided with the EU batch window
(02:00–03:10 UTC), and the contention degraded feature freshness for the
EU-morning dashboards — the exact consumers [[Cinder]] exists to serve. The
review ran across three sessions: the collision report, the candidate
windows, and the pick.

## Options considered

- **Stay at 02:00, throttle the batch.** Rejected: the batch is the
  billing-critical path; throttling it moved the problem instead of solving
  it.
- **Move to 03:00 UTC.** Rejected: the EU batch ends 03:10, so the tail
  overlap remained.
- **Move to 03:30 UTC.** Clears the batch end with margin, training still
  finishes before EU morning.

## Decision

Training runs at 03:30 UTC nightly ([[Priya Nair]] owns the window). The
schedule lives in
`repos/cinder/config/schedule.yaml`, and the batch planner output is the
source of truth for the window boundary.

## Consequences

- EU batch untouched (billing path keeps its window).
- Training SLA holds: 30 consecutive green nights as of 2026-07-08.
- EU-morning dashboards read features trained the same night.
- The window is now part of the on-call handover checklist when a training
  overruns — see the runbook.
""",
    },
    {
        "key": "drift dashboard embedding options",
        "sources": [
            (D(2026, 5, 5), 6,
             "drift dashboard embedding options\n"
             "the Superset license terms for embedded dashboards changed, so "
             "internal embeds are fine but the partner portal is now a gray "
             "zone.",
             [("Read", '{"file_path": "repos/drift/config/embed.yaml"}',
               "mode: superset_embed, audience: internal")]),
            (D(2026, 5, 26), 10,
             "drift dashboard embedding options\n"
             "options so far are an iframe per tool, the Superset embedded "
             "SDK, or a static exporter that rebuilds the dashboards on a "
             "schedule.",
             [("Bash", '{"command": "drift-exporter --help"}',
               "static rebuild, s3 target, 15m schedule")]),
            (D(2026, 6, 16), 4,
             "drift dashboard embedding options\n"
             "the partner portal moved to the static exporter today, with "
             "rebuilds every 15 minutes instead of the hourly full rebuild.",
             [("Read", '{"file_path": "repos/drift/config/embed.yaml"}',
               "mode: static_exporter, audience: partner+internal")]),
        ],
        "note_day": D(2026, 6, 30),
        "body": """# drift dashboard embedding options

[[Drift]] embeds go through a static exporter that rebuilds dashboards every 15
minutes, replacing the Superset-embedded setup.

## Context

The Superset license terms for embedded dashboards changed: internal embeds
stayed fine, but the partner portal landed in a gray zone nobody wanted to
explain to legal. The review ran across three sessions: the license problem,
the candidate approaches, and the cutover.

## Options considered

- **iframe per tool.** Rejected: each tool ships its own auth story and the
  row-level security policy would be enforced in three different places.
- **Superset embedded SDK.** Rejected: it is exactly the license exposure
  that started the review.
- **Static exporter.** Rebuilds the dashboards as static assets on a
  schedule; row-level security is enforced once, at export time.

## Decision

The exporter rebuilds dashboards as static assets every 15 minutes (the
refresh-cadence chain records the move from hourly). Row-level security is
applied at export time — `repos/drift/config/embed.yaml` is the config.

## Consequences

- No license exposure on the partner portal.
- Interactivity is limited to what a static asset can do — filter requests
  that need live queries route to the data API instead.
- The 15-minute rebuild replaced the hourly one (2026-06-30) because the
  exporter made it cheaper than the old full rebuild.
- Row-level security is enforced in exactly one place, which is what
  [[Lena Fischer]]'s policy review wanted all along.
""",
    },
    {
        "key": "data api versioning discussion",
        "sources": [
            (D(2026, 6, 2), 5,
             "data api versioning discussion\n"
             "two client teams broke on last week's schema change, so we need "
             "a versioning rule before the API leaves beta.",
             [("Bash", '{"command": "api-gateway breaks --last 30d"}',
               "breaking_changes: 3, affected_clients: 2")]),
            (D(2026, 6, 23), 8,
             "data api versioning discussion\n"
             "candidates are version in headers, media-type versioning, or "
             "URL paths, and I need the gateway implications for each.",
             [("Read", '{"file_path": "repos/api/gateway_rules.yaml"}',
               "routes: flat, no version negotiation")]),
            (D(2026, 7, 8), 6,
             "data api versioning discussion\n"
             "settled: URL paths with a 6-month deprecation window and sunset "
             "headers, and the gateway rules are drafted.",
             [("Read", '{"file_path": "repos/api/gateway_rules.yaml"}',
               "routes: /v1/..., sunset_headers: true, deprecation: 180d")]),
        ],
        "note_day": D(2026, 7, 22),
        "body": """# data api versioning discussion

The data API versions through URL paths (`/v1/...`) with a 6-month
deprecation window and sunset headers.

## Context

Two client teams broke on the same schema change in one week (one of them
[[Noah Bennett]]'s mobile clients), and the API
was about to leave beta without any versioning rule. The discussion ran
across three sessions: the break report, the candidate schemes, and the
gateway draft.

## Options considered

- **Version in request headers.** Rejected: invisible in logs, uncacheable
  at the gateway, and clients forgot to send it — the breaks would continue.
- **Media-type versioning.** Rejected: correct but expensive to adopt —
  every client's HTTP layer needs changes before the first versioned call.
- **URL paths.** Visible, cacheable, diffable in access logs, and the
  gateway can route and deprecate versions with plain rules.

## Decision

Versions live in the URL path (`/v1/...`). A deprecated version keeps
answering for 6 months with a `Sunset` header carrying the removal date,
then the gateway drops the route. The OpenAPI spec is generated in CI
(decision recorded 2026-06-11) so the docs cannot drift from the routes.

## Consequences

- Breaking changes ship as a new path, never as a surprise inside `/v1`.
- The 6-month window is a commitment the platform makes to client teams;
  the sunset date is machine-readable, so client dashboards can track it.
- Version count discipline: a new path needs a deprecation plan for the one
  it replaces, or the gateway rules reject the deploy.
""",
    },
    {
        "key": "incident review: atlas consumer lag",
        "sources": [
            (D(2026, 7, 14), 2,
             "incident review: atlas consumer lag\n"
             "lag alert firing on orders with p99 climbing past 40s, so I am "
             "looking at the consumer group events first.",
             [("Bash", '{"command": "atlas-admin consumers --describe"}',
               "group: orders-ingest, members: 12, rebalances_1h: 47")]),
            (D(2026, 7, 15), 5,
             "incident review: atlas consumer lag\n"
             "it was a rebalance storm during the deploy, and the mitigation "
             "is static group membership with the config staging now.",
             [("Read", '{"file_path": "repos/atlas/config/consumers.yaml"}',
               "static_membership: false")]),
            (D(2026, 7, 16), 9,
             "incident review: atlas consumer lag\n"
             "retro time: the timeline, the action items, and the deploy "
             "guard that should have caught this in the first place.",
             [("Bash", '{"command": "atlas-admin consumers --describe"}',
               "group: orders-ingest, rebalances_1h: 0")]),
        ],
        "note_day": D(2026, 7, 30),
        "body": """# incident review: atlas consumer lag

The 2026-07-14 consumer-lag incident was a rebalance storm triggered by a
deploy; static group membership and a deploy guard prevent recurrence.

## Context

During the 2026-07-14 deploy, the orders consumer group rebalanced 47 times
in an hour and p99 lag climbed past 40 seconds on the orders topic. The
incident ran three sessions: detection and triage on the 14th, mitigation on
the 15th, retro on the 16th. [[Beacon]]'s lag alert fired first (06:12 UTC).

## Timeline

- **06:12 UTC** — Beacon sev-2 lag alert on orders-ingest.
- **06:40 UTC** — Triage: every rebalance in the window correlated with a
  consumer restart from the deploy.
- **14:20 UTC** — Mitigation staged: static group membership for
  orders-ingest, rolling restart to apply it.
- **15:05 UTC** — Verification: p99 lag back under 2s, zero rebalances.
- **07-16** — Retro: action items and the deploy guard.

## Options considered

- **Pause deploys during peak windows.** Rejected: moves the failure, does
  not fix it — the storm would just happen at 02:00.
- **Sticky assignors.** Rejected: still rebalance, just more politely; the
  lag spikes survived the experiment.
- **Static group membership + a deploy guard.** Consumers keep their
  assignments across restarts; the guard blocks deploys that restart the
  group without the static flag set.

## Decision

Static group membership for [[Atlas]] consumers (decision recorded
2026-07-21)
plus the deploy guard in the rollback runbook. The config lives in
`repos/atlas/config/consumers.yaml`.

## Consequences

- Zero rebalances across the two deploys since; p99 lag stays under 2s
  (status note 2026-08-04).
- Deploys that touch consumers require the static flag — the guard makes
  forgetting it a build failure, not an incident.
- Follow-ups tracked in the weekly sync: extend the guard to Beacon's
  consumer, and document archive-offset replay for lag backfills.
""",
    },
    {
        "key": "atlas weekly sync",
        "sources": [
            (D(2026, 3, 9), 4,
             "atlas weekly sync\n"
             "agenda: throughput baseline, retention decision recap, and "
             "replay tool progress.",
             [("Bash", '{"command": "atlas-stats throughput --7d"}',
               "sustained: 31k msg/s, p99_lag: 1.4s")]),
            (D(2026, 6, 15), 7,
             "atlas weekly sync\n"
             "agenda: exactly-once tuning results, static membership "
             "follow-ups, and capacity for Q3.",
             [("Bash", '{"command": "atlas-stats throughput --7d"}',
               "sustained: 41k msg/s, p99_lag: 1.8s")]),
            (D(2026, 8, 17), 3,
             "atlas weekly sync\n"
             "agenda: the lag panel after the incident fixes, archive tier "
             "costs, and open action items.",
             [("Bash", '{"command": "atlas-stats throughput --7d"}',
               "sustained: 40k msg/s, p99_lag: 1.1s")]),
        ],
        "note_day": D(2026, 8, 31),
        "body": """# atlas weekly sync

Recurring state of [[Atlas]]: 40k msg/s sustained ingest, 21-day hot retention
with a quarterly archive tier, static consumer membership, and the replay
tool in production.

## Context

The Atlas weekly sync is the standing session where throughput, retention,
and incident follow-ups get reviewed. Three turns of it are captured here —
March, June, and August — and this note is the distilled state across
them, not minutes of any single meeting.

## State by topic

- **Throughput.** The baseline moved from ~31k msg/s (March) to 40k+ after
  the exactly-once tuning and the partition re-balance; the current chain
  records 40k as the number to defend.
- **Retention.** 21 days hot plus the quarterly archive tier (the retention
  discussion note has the full decision); the June sync signed off the Q3
  capacity against it.
- **Consumers.** Static group membership since the July incident; the sync
  tracks extending the deploy guard to Beacon as an open item.
- **Replay tool.** Shipped (2026-04-02) and documented in the runbook, with
  archive offsets supported since the retention change.

## Decisions and commitments

- 40k msg/s is the sustained baseline for capacity planning — regressions
  are incidents, not tuning opportunities.
- The archive tier cost review lands at the September sync.

## Open items

- Extend the deploy guard to Beacon's consumer group.
- Archive-offset replay for lag backfills (owner: [[Daniel Okafor]]).
- Capacity review for Q3 ingestion growth (owner: [[Tomas Rivera]]).
""",
    },
    {
        "key": "on-call rotation overhaul",
        "sources": [
            (D(2026, 3, 16), 9,
             "on-call rotation overhaul\n"
             "handover gaps keep dropping open sevs, so the rotation "
             "mechanics need a review before the next quarter starts.",
             [("Bash", '{"command": "rota-stats handovers --last 90d"}',
               "handovers: 12, dropped_sevs: 4, ack_sync: false")]),
            (D(2026, 4, 6), 5,
             "on-call rotation overhaul\n"
             "the proposal on the table is a single rota calendar with "
             "ack sync from Beacon and a written handover checklist.",
             [("Read", '{"file_path": "repos/beacon/config/rota.yaml"}',
               "rota: ad_hoc, ack_sync: false, handover_checklist: none")]),
            (D(2026, 4, 27), 7,
             "on-call rotation overhaul\n"
             "rotation v2 is live: one calendar owned by Iris, acks sync "
             "from Beacon, and the first week had zero dropped sevs.",
             [("Bash", '{"command": "rota-stats handovers --last 7d"}',
               "handovers: 1, dropped_sevs: 0, ack_sync: true")]),
        ],
        "note_day": D(2026, 5, 11),
        "body": """# on-call rotation overhaul

On-call rotation v2 runs one calendar owned by [[Iris Kovac]], with
[[Beacon]] acks syncing to the rota so handovers inherit open sevs.

## Context

The pre-v2 rotation was a set of ad-hoc swaps: 12 handovers in 90 days
dropped 4 open sevs because nothing tied an acknowledged alert to the
person taking over. The overhaul ran across three sessions: the gap
report, the single-calendar proposal, and the first live week.

## Options considered

- **Keep ad-hoc swaps, add a reminder bot.** Rejected: reminders do not
  transfer ownership; the dropped sevs were ownership gaps, not memory
  gaps.
- **Two calendars (weekday/weekend).** Rejected: the boundary is where
  sevs go to die; one calendar is auditable.
- **One rota calendar + ack sync + written handover.** [[Iris Kovac]]
  owns the calendar, [[Beacon]] acks update it, and the checklist makes
  the handover a readable artifact instead of a hallway chat.

## Decision

Rotation v2: a single rota calendar, acks sync from Beacon, handovers
follow the written checklist. Config lives in
`repos/beacon/config/rota.yaml`.

## Consequences

- Zero dropped sevs across the first weeks (status note 2026-05-18).
- Mid-week swaps must land in the rota before the swap — the runbook
  makes the ordering explicit.
- The weekly sync tracks extending the same ack-sync pattern to the
  [[Drift]] on-call as an open item.
""",
    },
    {
        "key": "kafka broker upgrade plan",
        "sources": [
            (D(2026, 6, 8), 6,
             "kafka broker upgrade plan\n"
             "the brokers are two minor versions behind and the support "
             "window for the current release closes this quarter.",
             [("Bash", '{"command": "atlas-admin brokers --version"}',
               "brokers: 12, version: 3.6.2, eol: 2026-09")]),
            (D(2026, 6, 29), 4,
             "kafka broker upgrade plan\n"
             "the plan is two weekend windows with static membership "
             "verified first, and a rollback image per broker.",
             [("Read", '{"file_path": "repos/atlas/runbooks/broker-upgrade.md"}',
               "windows: 2, prerequisite: static_membership, rollback: image_tag")]),
            (D(2026, 7, 6), 8,
             "kafka broker upgrade plan\n"
             "upgrade complete: all brokers on 3.8, consumer groups "
             "untouched, p99 lag flat through both windows.",
             [("Bash", '{"command": "atlas-admin brokers --version"}',
               "brokers: 12, version: 3.8.1, rebalances: 0")]),
        ],
        "note_day": D(2026, 7, 13),
        "body": """# kafka broker upgrade plan

Atlas brokers upgraded 3.6 → 3.8 across two weekend windows with zero
rebalances and flat p99 lag.

## Context

The broker fleet sat on 3.6.2 with support ending in September, and the
tiered-storage features the retention discussion wanted need 3.8+. The
plan ran across three sessions: the version audit, the two-window plan,
and the completion check.

## Options considered

- **One big-bang window.** Rejected: a 12-broker fleet does not roll
  back in one night, and the blast radius is the whole ingest path.
- **Upgrade in place, live.** Rejected: controller elections during
  peak ingest are exactly the failure mode the lag incident taught us
  to avoid.
- **Two weekend windows, pinned assignments, per-broker rollback image.**
  Consumer assignments stayed pinned across restarts (the sticky
  assignor pilot held the groups steady) and every broker had a staged
  rollback image, so a bad window rolls back per-node, not fleet-wide.

## Decision

Upgrade brokers to 3.8 in two weekend windows, prerequisite: consumer
assignments pinned, per-broker rollback image staged. The runbook lives
in `repos/atlas/runbooks/broker-upgrade.md`.

## Consequences

- Zero rebalances, p99 lag flat across both windows (status note
  2026-07-06).
- Tiered storage stays off for now — the archive tier decision covers
  the need without the new operational surface.
- The upgrade runbook is the template for the next fleet change;
  [[Daniel Okafor]] owns it.
""",
    },
    {
        "key": "cinder feature backfill tooling",
        "sources": [
            (D(2026, 4, 20), 3,
             "cinder feature backfill tooling\n"
             "the Q1 features need a backfill before the training "
             "cutoffs, but the hand-run scripts are not "
             "point-in-time-correct.",
             [("Bash", '{"command": "cinder-features list --stale"}',
               "stale_views: 9, cutoff: 2026-04-30")]),
            (D(2026, 5, 11), 6,
             "cinder feature backfill tooling\n"
             "the plan is a proper backfill job on Feast with dry-run "
             "point-in-time join reports before any write.",
             [("Bash", '{"command": "cinder-backfill --help"}',
               "usage: cinder-backfill --view <v> --from <date> --dry-run")]),
            (D(2026, 5, 18), 4,
             "cinder feature backfill tooling\n"
             "the job is merged behind PR 267 and the Q1 features are "
             "backfilled with validated point-in-time joins.",
             [("Read", '{"file_path": "repos/cinder/backfill/report.md"}',
               "views: 9, joins_validated: 9, mismatches: 0")]),
        ],
        "note_day": D(2026, 6, 1),
        "body": """# cinder feature backfill tooling

Cinder backfills run as a proper Feast job (PR 267) with dry-run
point-in-time join reports — the hand-run scripts are retired.

## Context

Nine Q1 feature views needed a backfill before their training cutoffs,
and the hand-run scripts silently leaked future rows into past windows —
not point-in-time-correct, which is disqualifying for training data.
The work ran across three sessions: the staleness audit, the job plan,
and the merged result.

## Options considered

- **Fix the hand-run scripts.** Rejected: they have no join reports and
  no way to prove correctness after the fact.
- **Backfill inside the nightly training job.** Rejected: couples the
  training SLA to backfill latency; a big backfill would starve the
  nightly run.
- **A dedicated Feast backfill job with dry-run reports.** Point-in-time
  joins validated against training cutoffs before any write lands.

## Decision

The backfill job (PR 267, approved by [[Priya Nair]]) runs on demand:
dry-run report first, real run after the joins validate. The runbook is
"How to backfill Cinder features".

## Consequences

- Q1 features backfilled with validated joins (status note 2026-05-21).
- Backfills no longer touch the training job, so the 03:30 UTC window
  SLA holds regardless of backfill load.
- The registry-write serialization fix came out of the same work —
  concurrent backfills no longer contend on the Feast registry lock.
""",
    },
    {
        "key": "partner portal launch checklist",
        "sources": [
            (D(2026, 6, 18), 7,
             "partner portal launch checklist\n"
             "the first partner cohort gets access next month, so the "
             "launch checklist needs to cover auth, data exposure, and "
             "the exporter path end to end.",
             [("Read", '{"file_path": "repos/herald/config/launch.yaml"}',
               "cohort: pilot_3, mode: exporter, auth: ember_service_tokens")]),
            (D(2026, 6, 25), 5,
             "partner portal launch checklist\n"
             "open items: row-level security spot-check per release, the "
             "sunset-date tracker for the API pages, and the read-only "
             "route audit.",
             [("Bash", '{"command": "herald routes --audit"}',
               "routes: 34, non_get: 0, rls_checked: partial")]),
            (D(2026, 7, 2), 9,
             "partner portal launch checklist\n"
             "checklist complete: exporter-only assets, read-only routes "
             "verified, RLS spot-check automated in the release job.",
             [("Read", '{"file_path": "repos/herald/config/launch.yaml"}',
               "cohort: pilot_3, rls_check: automated, status: ready")]),
        ],
        "note_day": D(2026, 7, 9),
        "body": """# partner portal launch checklist

The [[Herald]] launch checklist closed with exporter-only assets,
read-only routes verified, and an automated row-level security
spot-check per release.

## Context

The first partner cohort was scheduled for July, and the portal sits on
top of three systems at once: the [[Drift]] static exporter, [[Ember]]
service tokens, and the versioned data API. The checklist ran across
three sessions: the scoping, the open items, and the closure.

## Options considered

- **Ship on the Superset embed path.** Rejected: the license gray zone
  that already pushed the exporter decision.
- **Manual RLS review per release.** Rejected: reviews that depend on
  memory do not survive a busy release week; the check belongs in the
  release job.
- **Exporter-only assets + automated RLS spot-check.** No live queries,
  no embed path, and the security property is enforced where the assets
  are built.

## Decision

Launch checklist: exporter-only assets, read-only routes (audited: 0
non-GET), [[Ember]] service tokens, automated RLS spot-check in the
release job. Config lives in `repos/herald/config/launch.yaml`.

## Consequences

- Portal launched 2026-07-09 with zero CSP surprises (status note).
- [[Lena Fischer]]'s row-level security is enforced at export time and
  spot-checked on every release — two layers, both automatic.
- The sunset-date tracker ships with the portal so partner dashboards
  see API deprecations before they land.
""",
    },
    {
        "key": "h3 resolution tuning",
        "sources": [
            (D(2026, 5, 4), 5,
             "h3 resolution tuning\n"
             "the pilot regions on resolution 9 are accurate but the "
             "polygon count is exploding the alert evaluator's runtime.",
             [("Bash", '{"command": "beacon-stats geo --cells"}',
               "res9_cells: 41200, eval_p95: 9.4s")]),
            (D(2026, 5, 25), 8,
             "h3 resolution tuning\n"
             "resolution 7 halves the edge blur reports and keeps the "
             "evaluator under a second; the urban pilots stay on 9.",
             [("Bash", '{"command": "beacon-stats geo --cells"}',
               "res7_cells: 1830, eval_p95: 0.8s, blur_reports: down")]),
            (D(2026, 6, 8), 6,
             "h3 resolution tuning\n"
             "settled: resolution 7 is the default for every region, 9 "
             "stays an exception for the dense urban pilots only.",
             [("Read", '{"file_path": "repos/beacon/config/geo.yaml"}',
               "default_resolution: 7, exceptions: [urban_pilot]")]),
        ],
        "note_day": D(2026, 6, 12),
        "body": """# h3 resolution tuning

Beacon geo-alerts standardize on H3 resolution 7; resolution 9 remains
an exception for the dense urban pilots.

## Context

The H3 migration fixed border blur, but the pilot regions ran
resolution 9 and the cell count exploded the alert evaluator (p95
9.4s). The tuning ran across three sessions: the runtime report, the
resolution comparison, and the standardization.

## Options considered

- **Resolution 9 everywhere.** Rejected: evaluator runtime scales with
  cell count; 41k cells per evaluation is not sustainable.
- **Resolution 6 everywhere.** Rejected: the blur reports came back —
  resolution 7 was the floor the pilot data supported.
- **Resolution 7 default, 9 as a named exception.** The urban pilots
  keep 9 with an explicit config entry; everything else standardizes.

## Decision

Resolution 7 is the geo-alert default (`repos/beacon/config/geo.yaml`);
resolution 9 requires a named exception in the config. The decision is
recorded 2026-06-08 and the migration completed 2026-06-12.

## Consequences

- Evaluator p95 dropped 9.4s → 0.8s on the standard regions.
- Border blur reports stopped with the last polygon region.
- Adding a resolution-9 region now requires editing the exception list,
  which makes the cost visible at review time.
""",
    },
    {
        "key": "openapi client sdk rollout",
        "sources": [
            (D(2026, 7, 27), 4,
             "openapi client sdk rollout\n"
             "both client teams still hand-write API clients, and the "
             "versioning rules need generated SDKs to actually hold.",
             [("Bash", '{"command": "api-gateway clients --handwritten"}',
               "handwritten_clients: 2, drift_incidents_90d: 3")]),
            (D(2026, 8, 10), 7,
             "openapi client sdk rollout\n"
             "the generator reads the CI-published spec directly, and "
             "the first generated SDK passed the mobile team's review.",
             [("Read", '{"file_path": "repos/api/sdk/Makefile"}',
               "sdk: generated, source: ci_spec, hand_edits: forbidden")]),
            (D(2026, 8, 21), 5,
             "openapi client sdk rollout\n"
             "both teams shipped on generated SDKs with the sunset-date "
             "tracker wired into their dashboards.",
             [("Bash", '{"command": "api-gateway clients --handwritten"}',
               "handwritten_clients: 0")]),
        ],
        "note_day": D(2026, 8, 28),
        "body": """# openapi client sdk rollout

Both client teams moved to SDKs generated from the CI-published OpenAPI
spec; hand-written clients are retired.

## Context

The URL-path versioning decision only holds if clients actually track
versions, and hand-written clients drift — three drift incidents in 90
days. The rollout ran across three sessions: the drift audit, the
generator plan, and the cutover.

## Options considered

- **Publish a typed client library, maintained by us.** Rejected: a
  second source of truth; the spec in CI already is the contract.
- **Let each team keep hand-written clients.** Rejected: the drift
  incidents are the counterargument.
- **Generate SDKs from the CI spec.** The generator reads what CI
  published, nothing else, and the sunset-date tracker rides along.

## Decision

SDKs are generated from the CI-published spec (`make sdk VERSION=<v>`);
manual edits to generated code fail review. The semantic note records
the standing rule (2026-08-10); the rollout completed 2026-08-28.

## Consequences

- Hand-written clients: zero (verified in the cutover session).
- The sunset-date tracker gives client dashboards the deprecation
  clock the versioning decision promised.
- [[Noah Bennett]]'s mobile team validated the generator on the
  strictest client first, which flushed out the edge cases early.
""",
    },
    {
        "key": "ember token refresh outage",
        "sources": [
            (D(2026, 8, 19), 3,
             "ember token refresh outage\n"
             "support paged at 09:03 UTC: mobile clients stuck in a "
             "refresh loop, 401 rates climbing on the data API.",
             [("Bash", '{"command": "ember-audit refresh --rate --last 1h"}',
               "refresh_loops: 2870, 401_rate: 12%, families_affected: 340")]),
            (D(2026, 8, 20), 6,
             "ember token refresh outage\n"
             "the morning deploy dropped the per-family lock — winner "
             "and loser refreshes invalidated each other in a loop.",
             [("Read", '{"file_path": "repos/ember/config/tokens.yaml"}',
               "per_family_lock: false  # regression from 08-19 deploy")]),
            (D(2026, 8, 21), 4,
             "ember token refresh outage\n"
             "recovered: lock restored, affected families force-"
             "refreshed, clients recovered without re-login, retro "
             "actions filed.",
             [("Bash", '{"command": "ember-audit refresh --rate --last 1h"}',
               "refresh_loops: 0, 401_rate: baseline, families_recovered: 340")]),
        ],
        "note_day": D(2026, 9, 1),
        "body": """# ember token refresh outage

The 2026-08-19 refresh outage was a dropped per-family lock after a
deploy; recovery force-refreshed the affected families and the deploy
guard now asserts the lock's presence.

## Context

The morning deploy on 2026-08-19 shipped a refresh-path refactor that
silently dropped the per-family serialization: two refreshes on one
token family invalidated each other, and every client retried in a
loop. The outage ran three sessions: detection and triage on the 19th,
mitigation on the 20th, retro on the 21st. [[Noah Bennett]] correlated
the client-side symptoms within minutes.

## Timeline

- **09:03 UTC** — Support page: refresh loops, 401s climbing.
- **09:40 UTC** — Triage: the deploy config shows
  `per_family_lock: false` — a regression, not a client bug.
- **08-20** — Mitigation: serializer rolled back, lock restored, 340
  affected families force-refreshed.
- **08-21** — Retro: deploy-guard assertion, refresh-loop alarm,
  client-side backoff cap.

## Options considered

- **Client-side retry fix only.** Rejected: the loop is server-caused;
  a client patch masks the next regression of the same class.
- **Lock restored, nothing else.** Rejected: nothing would catch the
  next deploy that drops it — the guard must assert the invariant.
- **Lock restored + deploy-guard assertion + loop alarm + backoff cap.**
  Defense at the server, detection in the metrics, kindness at the
  client.

## Decision

The per-family lock is asserted by the deploy guard (a build failure if
absent), a refresh-loop alarm watches the audit metrics, and the mobile
clients cap refresh backoff (agreed with [[Noah Bennett]]).

## Consequences

- All 340 affected families recovered without re-login; no data loss,
  4 hours sev-2.
- The outage validated the rotation-v2 path: the rota inherited the
  open sev across the Monday handover without a drop.
- [[Sam Whitaker]] added the lock assertion to the security checklist
  for every Ember auth-path change.
""",
    },
    {
        "key": "atlas archive tier cost review",
        "sources": [
            (D(2026, 8, 31), 7,
             "atlas archive tier cost review\n"
             "the September sync needs the archive tier numbers: two "
             "quarters of data are in the vaults and the bill shape is "
             "still guesswork.",
             [("Bash", '{"command": "atlas-admin archive --costs"}',
               "vaults: 2, size_tb: 41, monthly: 3.2k, retrieves: 180")]),
            (D(2026, 9, 4), 5,
             "atlas archive tier cost review\n"
             "the retrieve pattern is the surprise: audits drive 80% of "
             "retrieves and they only touch the last quarter.",
             [("Bash", '{"command": "atlas-admin archive --retrieves --by-age"}',
               "last_quarter: 80%, older: 20%, p95_restore: 11h")]),
            (D(2026, 9, 9), 6,
             "atlas archive tier cost review\n"
             "the proposal for the sync: keep quarterly vaults, move "
             "the audit window to warm storage, and cap retrieves with "
             "a budget alert.",
             [("Read", '{"file_path": "repos/atlas/config/archive.yaml"}',
               "audit_window: last_quarter_warm, retrieve_budget_alert: on")]),
        ],
        "note_day": D(2026, 9, 11),
        "body": """# atlas archive tier cost review

The archive tier stays quarterly, with the audit window moved to warm
storage and a retrieve budget alert — the September sync's open item,
closed with numbers.

## Context

Two quarters of partitions sit in the Glacier vaults and the weekly
sync committed to a cost review in September. The review ran across
three sessions: the bill shape, the retrieve-pattern breakdown, and the
proposal the sync will sign off.

## Options considered

- **Everything to deep archive.** Rejected: restores are already the
  slow path (p95 11h); deep archive doubles it for a saving the audit
  pattern does not need.
- **Drop the archive tier entirely.** Rejected: the replay tool and the
  dedup audits depend on older offsets; the retention discussion
  settled that two quarters ago.
- **Quarterly vaults + warm audit window + retrieve budget alert.**
  Audits read warm (fast, predictable), everything else stays cold,
  and a runaway retrieve pattern pages before the bill does.

## Decision

Keep the quarterly vault layout; the last quarter lives in warm
storage for the audit pattern; retrieves carry a budget alert in
`repos/atlas/config/archive.yaml`.

## Consequences

- The retrieve bill concentrates where the audits actually read —
  the warm window covers 80% of retrieves at access-latency cost.
- The budget alert closes the "guesswork" part of the bill shape.
- [[Tomas Rivera]] signs the Q4 capacity against these numbers at the
  September sync; [[Daniel Okafor]] owns the runbook update.
""",
    },
    {
        "key": "dbt model naming standards",
        "sources": [
            (D(2026, 3, 30), 6,
             "dbt model naming standards\n"
             "the same model name means different things across the "
             "drift and cinder repos, and the confusion reached a "
             "partner-facing dashboard last week.",
             [("Bash", '{"command": "dbt-ls --duplicates"}',
               "name_collisions: 11, repos: [drift, cinder]")]),
            (D(2026, 4, 13), 9,
             "dbt model naming standards\n"
             "the proposal is the staging/mart split with enforced "
             "prefixes and a linter that fails the build on collision.",
             [("Read", '{"file_path": "repos/drift/dbt_project.yml"}',
               "naming: none, layers: flat")]),
            (D(2026, 5, 4), 7,
             "dbt model naming standards\n"
             "the linter is merged behind PR 195, both repos are "
             "renamed, and the collision count is zero.",
             [("Bash", '{"command": "dbt-ls --duplicates"}',
               "name_collisions: 0, prefix_violations: 0")]),
        ],
        "note_day": D(2026, 5, 18),
        "body": """# dbt model naming standards

dbt models follow the staging/mart split with a linter (PR 195) that
fails the build on name collisions or prefix violations.

## Context

Eleven model names collided across the [[Drift]] and [[Cinder]] repos —
the same name meant a staging view in one and a published mart in the
other, and the confusion reached a partner-facing dashboard. The work
ran across three sessions: the collision audit, the split proposal, and
the linter merge.

## Options considered

- **Renumber the collisions as they surface.** Rejected: whack-a-mole;
  the audit found eleven and nobody believes that is all of them.
- **Move everything into one dbt project.** Rejected: the deploy
  cadences differ and the merge would be a quarter of churn for
  hygiene.
- **Staging/mart split + a build-failing linter per repo.** Enforced at
  the point where a bad name can actually merge.

## Decision

Staging models clean (`stg_` prefix), marts publish (`mart_` prefix),
and the linter fails the build on collisions or violations in both
repos. [[Lena Fischer]] merged PR 195.

## Consequences

- Collisions: zero since the rename; the semantic note records the
  standing rule.
- The partner-facing confusion class is closed: a mart name resolves
  to exactly one published model.
- New repos adopt the linter on day one — the checklist in the portal
  launch references it.
""",
    },
    {
        "key": "beacon sev definitions recalibration",
        "sources": [
            (D(2026, 6, 1), 5,
             "beacon sev definitions recalibration\n"
             "the sev definitions predate the routing split, and sev-2 "
             "is doing double duty: it pages for some teams and informs "
             "for others.",
             [("Bash", '{"command": "beacon-stats sevs --last 30d"}',
               "sev2_total: 214, paged: 96, informed: 118")]),
            (D(2026, 6, 22), 8,
             "beacon sev definitions recalibration\n"
             "the recalibration ties each sev to a response window "
             "instead of a team: 5m for sev-1, 15m for sev-2, the "
             "working day for sev-3.",
             [("Read", '{"file_path": "repos/beacon/config/sev.yaml"}',
               "defs: team_scoped, proposal: response_time_scoped")]),
            (D(2026, 7, 13), 6,
             "beacon sev definitions recalibration\n"
             "shipped: definitions are response-time scoped, the routing "
             "table maps from them, and the page-vs-inform split is "
             "gone from team folklore.",
             [("Bash", '{"command": "beacon-stats sevs --last 7d"}',
               "sev2_total: 41, paged: 41, informed: 0")]),
        ],
        "note_day": D(2026, 7, 27),
        "body": """# beacon sev definitions recalibration

Beacon severities are response-time scoped (5m / 15m / working day) and
the routing table maps from them — sev-2 pages, always, for everyone.

## Context

The sev definitions predated the routing split and were team-scoped:
sev-2 paged for some teams and merely informed for others, which made
the page-vs-inform boundary folklore instead of config. The
recalibration ran across three sessions: the double-duty report, the
response-time proposal, and the ship.

## Options considered

- **Per-team sev overrides.** Rejected: more config surface, same
  folklore, and cross-team alerts still ambiguous.
- **Add a sev-4 for informational.** Rejected: renames the problem —
  the ambiguity was in sev-2, not a missing level.
- **Response-time scoped definitions.** A sev is what the responder
  owes: 5 minutes, 15 minutes, the working day. Routing maps from the
  definition, so the split is mechanical.

## Decision

Severity definitions live in `repos/beacon/config/sev.yaml`, scoped by
response time; the semantic note records the meanings. [[Iris Kovac]]
signed off on the rota implications.

## Consequences

- sev-2 pages uniformly (41/41 paged in the first week, zero
  informed-only).
- The ack-sync expectation per sev is now stated in config, not in
  onboarding folklore.
- The geo-alert and lag panels map their alerts to the shared
  definitions, which ended the one-off severity debates.
""",
    },
]

# -------------------------------------------------------------- PR reviews --

PR_REVIEWS = [
    ("Maya requests changes on Atlas PR 482", D(2026, 6, 24),
     "Maya blocked the consumer rebalance patch: no test reproduces the "
     "rebalance it claims to fix. Requested a static-membership test before "
     "re-review."),
    ("Atlas PR 501 adds static group membership", D(2026, 7, 20),
     "The follow-up to the incident: consumers register with static "
     "membership and a guard test fails any restart without the flag. "
     "Approved by [[Maya Chen]] two days after the mitigation."),
    ("Maya approves Ember audit log PR 388", D(2026, 3, 25),
     "The audit log writer PR merged with the 90-day retention default from "
     "the original compliance sign-off. [[Sam Whitaker]] signed off on the "
     "security checklist."),
    ("Sam reviews Beacon routing table PR 141", D(2026, 4, 9),
     "[[Sam Whitaker]] reviewed the sev routing table change: the ack-sync "
     "hook needed its own test, which landed in the same PR."),
    ("Cinder backfill job PR 267 approved", D(2026, 5, 20),
     "[[Priya Nair]] approved the Q1 feature backfill job; the runbook for "
     "backfills links to it."),
    ("Drift row-level security PR 93 merged", D(2026, 6, 2),
     "Row-level security enforcement moved to export time in the exporter "
     "PR; [[Lena Fischer]] merged it ahead of the partner-portal cutover."),
    ("Iris approves the rota ack-sync PR 162", D(2026, 5, 12),
     "[[Iris Kovac]] approved the rota-side ack-sync hook; the handover "
     "inherits open sevs only when the rota entry exists, so the PR added "
     "a missing-rota test too."),
    ("Noah requests changes on the Ember refresh PR 402", D(2026, 4, 10),
     "[[Noah Bennett]] blocked the refresh serialization patch: the "
     "client-side retry budget would have tripled. It landed only after "
     "the backoff curve changed."),
    ("Beacon H3 migration PR 177 merged", D(2026, 5, 28),
     "The last production region moved to H3 cells; the polygon matcher "
     "stays behind a flag for audit replays. Reviewed by [[Maya Chen]]."),
    ("Kafka upgrade runbook PR 210 merged", D(2026, 6, 20),
     "The broker upgrade runbook covers the two-window path and the "
     "assignment checks before each restart. Owner: [[Daniel Okafor]]."),
    ("Partner portal checklist PR 228 merged", D(2026, 7, 1),
     "The launch checklist for [[Herald]]: exporter-only assets, "
     "[[Ember]] service tokens, and the row-level security spot-check per "
     "release."),
    ("dbt naming linter PR 195 merged", D(2026, 5, 12),
     "The staging/mart prefix linter gates both the [[Drift]] and "
     "[[Cinder]] repos; violations fail the build. [[Lena Fischer]] "
     "merged it."),
]

# ------------------------------------------------------------- incident arc --

INCIDENT = [
    ("Atlas consumer lag incident: detection", D(2026, 7, 14),
     "[[Beacon]] sev-2 fired at 06:12 UTC: p99 lag on the orders topic past "
     "40s. First suspect was a slow partition; the consumer group events "
     "told another story."),
    ("Atlas consumer lag incident: triage", D(2026, 7, 14),
     "47 rebalances in one hour, all correlated with consumer restarts from "
     "the morning deploy. The storm, not the partitions, was starving "
     "throughput."),
    ("Atlas consumer lag incident: mitigation", D(2026, 7, 15),
     "Staged static group membership for orders-ingest and rolled it out. "
     "Rebalances stopped the moment consumers kept their assignments across "
     "restarts."),
    ("Atlas consumer lag incident: verification", D(2026, 7, 15),
     "p99 lag back under 2s within the first partition cycle after the "
     "mitigation; dedup checks on the orders topic stayed green throughout."),
    ("Atlas consumer lag incident: retro", D(2026, 7, 16),
     "Action items: static membership as the default (done 07-21), a deploy "
     "guard for consumer restarts, and the lag-panel runbook update. No "
     "data loss, 9 hours sev-2."),
    ("Ember token refresh outage: detection", D(2026, 8, 19),
     "Support paged at 09:03 UTC: mobile clients stuck in a refresh loop, "
     "401s climbing on the data API. [[Noah Bennett]] correlated the "
     "client-side symptoms within minutes."),
    ("Ember token refresh outage: triage", D(2026, 8, 19),
     "The morning deploy dropped the per-family lock — winner and loser "
     "refreshes on one token family invalidated each other in a loop."),
    ("Ember token refresh outage: mitigation", D(2026, 8, 20),
     "Rolled the serializer back, restored the per-family lock, and "
     "force-refreshed the 340 affected families; clients recovered without "
     "re-login."),
    ("Ember token refresh outage: retro", D(2026, 8, 21),
     "Action items: a lock-presence assertion in the deploy guard, a "
     "refresh-loop alarm on [[Ember]], and a client-side backoff cap with "
     "[[Noah Bennett]]. 4 hours sev-2, no data loss."),
]

# --------------------------------------------------------------- debugging --

DEBUGGING = [
    ("Duplicate events in the orders topic root cause", D(2026, 3, 3),
     "Not a Kafka bug: a hand-run backfill script replayed a window of the "
     "orders topic twice while exactly-once was disabled on that path. "
     "Follow-up: the replay tooling in progress must refuse double replays "
     "by default."),
    ("Token refresh race in Ember traced", D(2026, 4, 8),
     "Two refresh requests raced on the same token family; the loser "
     "invalidated the winner. Fixed with per-family refresh serialization "
     "in [[Ember]]."),
    ("Kafka rebalance storm during a deploy", D(2026, 7, 14),
     "Traced the 06:12 lag spike to a rebalance storm: every consumer "
     "restart from the deploy reassigning the whole group. Led to the "
     "static-membership decision."),
    ("Iceberg partition spec mismatch backfill", D(2026, 6, 26),
     "A table rewritten with a new partition spec rejected the old files' "
     "manifests. The backfill re-registered the manifests; the spec change "
     "now ships with a migration note."),
    ("Ember refresh loop root cause", D(2026, 8, 19),
     "The refresh outage was a missing per-family lock after a deploy: "
     "winner and loser invalidated each other, so every client retried in "
     "a loop. Fixed with the deploy-guard lock assertion."),
    ("Feast registry lock contention in Cinder", D(2026, 4, 22),
     "Concurrent backfills contended on the Feast registry lock and "
     "stalled a nightly training. Registry writes are serialized now; the "
     "contention is gone."),
    ("Static exporter served a stale manifest", D(2026, 6, 23),
     "The partner portal showed yesterday's dashboards: the exporter "
     "published assets before the manifest flip. The publish now flips "
     "the manifest last, atomically."),
    ("CSP header broke the Superset embed", D(2026, 5, 7),
     "A platform-wide CSP rollout blanked the embedded dashboards for an "
     "afternoon. The incident pushed the static-exporter decision forward "
     "by a quarter."),
]

# --------------------------------------------------------------- migrations --

MIGRATIONS = [
    ("Feast 0.38 to 0.40 migration completed", D(2026, 5, 5),
     "[[Cinder]]'s Feast upgrade landed: registry format migration plus the "
     "point-in-time join fix backported. Trainings green since."),
    ("Drift dashboards moved to the static exporter", D(2026, 6, 16),
     "Partner-portal embeds on [[Drift]] cut over to the static exporter; "
     "the Superset embedded setup is retired on the internal side too."),
    ("Kafka brokers upgraded to 3.8", D(2026, 7, 6),
     "Two weekend windows, zero consumer impact — pinned assignments kept "
     "the groups intact through the broker restarts."),
    ("Beacon geo-alerts migrated to H3 cells", D(2026, 6, 12),
     "The postcode-polygon matcher retired after the last region moved; "
     "audit replays keep it behind a feature flag."),
]

# -------------------------------------------------------------------- hubs --
# (stable filename, legacy file mtime, human body) — Alex's pre-seahorse
# notes, migrated by `seahorse frontmatter migrate` in one run. The mtime
# becomes created_at/valid_at, so these bodies only state what was true
# when the legacy files were last touched in 2025.

HUBS = [
    ("Northwind Analytics", D(2025, 6, 2),
     "The employer. The data platform group builds the ingestion ([[Atlas]]), "
     "alerting ([[Beacon]]), features ([[Cinder]]), dashboards ([[Drift]]) "
     "and auth ([[Ember]]) stack on the lakehouse.\n\n"
     "Team notes live in the people notes; project overviews in the project "
     "notes."),
    ("Alex Vega", D(2025, 7, 14),
     "Data engineer, remote. Works on [[Atlas]] ingestion and helps across "
     "the platform. This vault is my working memory — the agent writes to "
     "it, I correct it."),
    ("Maya Chen", D(2025, 7, 14),
     "Colleague on the data platform team. Owns [[Beacon]]. Default reviewer "
     "for [[Atlas]] PRs; her rule: no merge without a test."),
    ("Tomas Rivera", D(2025, 7, 14),
     "Runs the data platform group at [[Northwind Analytics]]. 1:1s on "
     "Thursdays. Owns the storage budget sign-offs."),
    ("Priya Nair", D(2025, 7, 14),
     "Colleague on the data platform team. Owns [[Cinder]] and its training "
     "schedules."),
    ("Daniel Okafor", D(2025, 7, 14),
     "Colleague on the data platform team. Owns the [[Atlas]] consumer group "
     "and the replay tool."),
    ("Lena Fischer", D(2025, 7, 14),
     "Colleague on the data platform team. Owns [[Drift]] and the row-level "
     "security policy."),
    ("Sam Whitaker", D(2025, 7, 14),
     "Colleague on the data platform team. Owns [[Ember]] and the security "
     "checklist for auth changes."),
    ("Atlas", D(2025, 8, 20),
     "The ingestion platform: Kafka into Iceberg on S3. Owned by the data "
     "platform group; consumers by [[Daniel Okafor]]."),
    ("Beacon", D(2025, 8, 20),
     "The alerting service on Atlas streams. Owned by [[Maya Chen]]."),
    ("Cinder", D(2025, 8, 20),
     "The nightly ML feature pipeline. Feast + dbt. Owned by "
     "[[Priya Nair]]."),
    ("Drift", D(2025, 8, 20),
     "The dashboards layer. dbt-defined metrics with row-level security. "
     "Owned by [[Lena Fischer]]."),
    ("Ember", D(2025, 8, 20),
     "Auth and access control for the data API. Owned by [[Sam Whitaker]]."),
    ("Iris Kovac", D(2025, 7, 14),
     "Colleague on the data platform team. Runs the on-call rota and the "
     "incident review calendar; [[Beacon]] acks sync to her rota."),
    ("Noah Bennett", D(2025, 7, 14),
     "Colleague on the data platform team. Owns the mobile clients "
     "against the data API and the [[Ember]] token lifecycle."),
    ("Herald", D(2025, 9, 2),
     "The partner-facing data portal: read-only dashboards served from "
     "the [[Drift]] static exporter, authenticated with [[Ember]]."),
]

# ---------------------------------------------------------------- showcase --
# The hand-curated home-city pair (vault root, not Memory/): a mirror of
# the engine's own Madrid→Barcelona succession, kept as the demo showcase.
# Bodies are byte-locked (README snippet + demo clip quote them); only the
# frontmatter is regenerated. {barcelona_id} is filled with Barcelona's
# generated UUIDv7 (the legacy comment cited a pre-1.0.0 non-UUIDv7 id).
# Neither body ends with a trailing newline.

SHOWCASE_MADRID = {
    "file": "2026-05-10-persona-home-city.md",
    "title": "Alex Vega lives in Madrid",
    "recorded_day": D(2026, 5, 10),   # created_at — when the agent recorded it
    "fact_day": D(2026, 2, 14),       # valid_at — the move happened in February
    "invalidated_day": D(2026, 8, 30),
    "summary": "Alex Vega moved to Madrid in February 2026; invalidated when "
               "a later episode recorded the move to Barcelona.",
    "body": "\n# Alex Vega lives in Madrid\n\n"
            "Alex Vega lives in Madrid (moved there in February 2026), works "
            "remotely as a\n"
            "data engineer.\n\n"
            "<!-- Superseded on 2026-08-30 by episode\n"
            "     {barcelona_id} (\"Alex Vega lives in Barcelona\").\n"
            "     invalid_at was set by the superseding correction; the "
            "episode is kept,\n"
            "     never erased. -->",
}

SHOWCASE_BARCELONA = {
    "file": "2026-08-30-persona-home-city.md",
    "title": "Alex Vega lives in Barcelona",
    "fact_day": D(2026, 8, 30),       # valid_at — the correction's --valid-at
    "summary": "Alex Vega moved from Madrid to Barcelona on 2026-08-30. "
               "Supersedes the previous home-city episode; history preserved.",
    "body": "\n# Alex Vega lives in Barcelona\n\n"
            "Alex Vega moved to Barcelona on 2026-08-30 (previously Madrid, "
            "February 2026 —\n"
            "August 2026), still working remotely as a data engineer.",
}
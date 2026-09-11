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
    ("Tomas Rivera runs the platform group", D(2026, 3, 2),
     "[[Tomas Rivera]] runs the weekly platform sync and owns the storage "
     "budget sign-offs, including the [[Atlas]] retention reviews."),
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
    ("Beacon alert acks sync to the on-call rota", D(2026, 5, 14),
     "Acknowledging a Beacon alert updates the on-call rota so handovers "
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
]

# ------------------------------------------------------------------ status --
# (title, day, body) — episodic state snapshots; valid_at = the day.

STATUS = [
    ("Atlas status: consumer lag under 2s p99", D(2026, 8, 4),
     "Post-incident check across the last deploys: p99 consumer lag sits at "
     "1.1s with zero rebalances since static membership landed. The lag "
     "panel is the deploy gate now."),
    ("Atlas status: replay tool shipped", D(2026, 4, 2),
     "The replay tool landed: dry-run reports, dedup checks, and "
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
]

# ---------------------------------------------------------------- semantic --
# Standing knowledge and preferences — no valid_at (no time anchor).

SEMANTIC = [
    ("The lakehouse is Iceberg on S3", D(2026, 2, 14),
     "All Atlas tables land as Iceberg on S3 — the lakehouse contract every "
     "project builds on."),
    ("Production runs on a managed Kubernetes cluster", D(2026, 2, 25),
     "Production workloads run on the managed Kubernetes cluster; deploys "
     "are manifests, never snowflakes."),
    ("The platform org uses trunk-based development", D(2026, 3, 5),
     "Short-lived branches into main, reviewed within a day — Maya's review "
     "rule is the real gate."),
    ("On-call rotation is weekly, starting Mondays", D(2026, 3, 24),
     "The on-call handover runs Mondays; Beacon acks sync to the rota so "
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
     "3. Watch the lag panel for one partition cycle\n"
     "4. Re-open the deploy ticket with the observed reason"),
    ("How to replay an Atlas topic into Iceberg", D(2026, 5, 12),
     "Dry-run first; the dedup report is the contract.\n\n"
     "1. `atlas-replay --topic orders --from <offset> --dry-run`\n"
     "2. Check the dedup report the tool prints (exactly-once does the rest)\n"
     "3. Run without `--dry-run`, then compare row counts in Iceberg"),
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

Atlas keeps 21 days of hot Kafka data and moves older partitions to a quarterly archive tier.

## Context

The original 30-day flat retention filled the lakehouse budget two quarters
running, and the budget review made it clear the next sign-off (Tomas) would
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
reports reached support faster than the tokens expired. The mobile team
escalated, and the review ran across three sessions: the problem report,
the proposal, and the security clearance.

## Options considered

- **Keep 15 minutes, tune clients.** Rejected: the refresh traffic is
  inherent to the TTL, not a client bug, and the escalation had executive
  attention.
- **1-hour TTL, no rotation.** Rejected by the security review: a leaked
  token would stay valid an hour with no revocation signal.
- **1-hour TTL + rotation + refresh tokens.** Rotation emits an audit entry
  per rotation, which gives the revocation signal the review wanted.

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

Beacon routes sev-1 and sev-2 to PagerDuty and sev-3 to Slack, with alert acks synced to the on-call rota.

## Context

Slack-only routing meant every alert landed in the same channel: 812 alerts
in one week, 41% acknowledged within five minutes, and the sev pages that
mattered were getting scrolled past. The review ran across three sessions:
the noise report, the routing proposal, and the tuning pass after the first
week.

## Options considered

- **Everything to PagerDuty.** Rejected: the false-positive rate would have
  burned the rota within a sprint.
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
EU-morning dashboards — the exact consumers Cinder exists to serve. The
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

Training runs at 03:30 UTC nightly. The schedule lives in
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

Drift embeds go through a static exporter that rebuilds dashboards every 15 minutes, replacing the Superset-embedded setup.

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

The data API versions through URL paths (`/v1/...`) with a 6-month deprecation window and sunset headers.

## Context

Two client teams broke on the same schema change in one week, and the API
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

The 2026-07-14 consumer-lag incident was a rebalance storm triggered by a deploy; static group membership and a deploy guard prevent recurrence.

## Context

During the 2026-07-14 deploy, the orders consumer group rebalanced 47 times
in an hour and p99 lag climbed past 40 seconds on the orders topic. The
incident ran three sessions: detection and triage on the 14th, mitigation on
the 15th, retro on the 16th. Beacon's lag alert fired first (06:12 UTC).

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

Static group membership for Atlas consumers (decision recorded 2026-07-21)
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

Recurring state of Atlas: 40k msg/s sustained ingest, 21-day hot retention with a quarterly archive tier, static consumer membership, and the replay tool in production.

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
]

# ------------------------------------------------------------- incident arc --

INCIDENT = [
    ("Atlas consumer lag incident: detection", D(2026, 7, 14),
     "Beacon sev-2 fired at 06:12 UTC: p99 lag on the orders topic past 40s. "
     "First suspect was a slow partition; the consumer group events told "
     "another story."),
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
     "in Ember."),
    ("Kafka rebalance storm during a deploy", D(2026, 7, 14),
     "Traced the 06:12 lag spike to a rebalance storm: every consumer "
     "restart from the deploy reassigning the whole group. Led to the "
     "static-membership decision."),
    ("Iceberg partition spec mismatch backfill", D(2026, 6, 26),
     "A table rewritten with a new partition spec rejected the old files' "
     "manifests. The backfill re-registered the manifests; the spec change "
     "now ships with a migration note."),
]

# --------------------------------------------------------------- migrations --

MIGRATIONS = [
    ("Feast 0.38 to 0.40 migration completed", D(2026, 5, 5),
     "Cinder's Feast upgrade landed: registry format migration plus the "
     "point-in-time join fix backported. Trainings green since."),
    ("Drift dashboards moved to the static exporter", D(2026, 6, 16),
     "Partner-portal embeds cut over to the static exporter; the Superset "
     "embedded setup is retired on the internal side too."),
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
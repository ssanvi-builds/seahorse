#!/usr/bin/env bash
#
# seed-vault.sh — populate the demo-clip vault with a dense, connected
# fictional scenario BEFORE the tape renders, so the Obsidian take (F3) shows
# a lived-in vault: ~14 episodes, two supersedes chains, one soft-delete,
# consolidated knowledge notes, and root hub notes that make the Obsidian
# graph view resolve its [[wikilinks]] into real edges.
#
# All content is fictional (the same invented cast as examples/demo-vault/:
# Northwind Analytics, Alex Vega, Maya Chen, Project Cinder, Atlas) and
# public-safe. Nothing here is Sergio's data.
#
# The tape's live acts write on top of this: it remembers Alex Vega joining
# Northwind (do NOT seed that exact fact), improves it, and writes the Beacon
# alert-routing ADR. That is why the seed's Alex episode says "owns the
# billing ETL" — similar world, different facts.
#
# Usage: seed-vault.sh <vault-path>  (SEAHORSE_VAULT must already point at it,
# config written, [materialize] configured by render-demo.sh)

set -euo pipefail

VAULT="${1:?usage: seed-vault.sh <vault-path>}"
if [[ ! -d "$VAULT/.seahorse" ]]; then
  echo "not a seahorse vault: $VAULT" >&2
  exit 1
fi

if [[ -x "$(dirname "$(readlink -f "$0")")/../../.venv/bin/seahorse" ]]; then
  SEAHORSE="$(dirname "$(readlink -f "$0")")/../../.venv/bin/seahorse"
fi
: "${SEAHORSE:=seahorse}"

quiet() { "$@" >/dev/null; }

echo "seed: cinder cluster (3 episodes)"
quiet "$SEAHORSE" remember "Project Cinder migrates billing from Postgres 12 to 15 in Q3" --title cinder
quiet "$SEAHORSE" remember "Cinder dry-run showed a 4 hour downtime window on the staging replica" --title cinder
quiet "$SEAHORSE" remember "Cinder cutover owner is Maya Chen; rollback plan is a logical replication swap" --title cinder

echo "seed: atlas cluster (3 episodes)"
quiet "$SEAHORSE" remember "The Atlas dashboards scrape Prometheus exporters every 30 seconds" --title atlas
quiet "$SEAHORSE" remember "Atlas alert thresholds were tuned after the March capacity review" --title atlas
quiet "$SEAHORSE" remember "Atlas is read-only during the [[Project Cinder]] cutover window" --title atlas

echo "seed: context episodes (wikilinks feed the graph)"
quiet "$SEAHORSE" remember "Maya Chen leads the Atlas project at [[Northwind Analytics]]" --title maya
quiet "$SEAHORSE" remember "Alex Vega owns the billing ETL at [[Northwind Analytics]]" --title alex-etl
quiet "$SEAHORSE" remember "[[Northwind Analytics]] requires production deploys to pass a canary stage" --title deploy-policy
quiet "$SEAHORSE" remember "Quarterly billing review is scheduled the first Tuesday after close" --title billing-review

echo "seed: correction chain + soft delete"
EP_ATLAS="$("$SEAHORSE" recall 'atlas dashboards scrape' | awk 'length($2)==36 && $3=="atlas" {print $2; exit}')"
quiet "$SEAHORSE" improve "$EP_ATLAS" "The Atlas dashboards scrape Prometheus exporters every 10 seconds since the March tuning" --reason correction
EP_DRY="$("$SEAHORSE" recall 'cinder downtime window' | awk 'length($2)==36 && $3=="cinder" {print $2; exit}')"
quiet "$SEAHORSE" forget "$EP_DRY" --reason "superseded by the June re-run"

echo "seed: ADR + session note (project_doc)"
quiet "$SEAHORSE" remember "ADR: Cinder cutover window. Context: the 4 hour staging window. Options: weekend freeze vs logical replication swap. Decision: logical replication swap, Maya Chen owns it. Consequences: Atlas stays read-only; rollback is one command." --cognitive-type project_doc --title "ADR: Cinder cutover window"
quiet "$SEAHORSE" remember "Session note: Cinder dry-run review. Decided: keep the June window. Evidence: dry-run timings, on-call sign-off. Open questions: canary coverage for the billing ETL." --cognitive-type project_doc --title "Session: Cinder dry-run review"

echo "seed: consolidate (two 3-member clusters)"
quiet "$SEAHORSE" consolidate

echo "seed: hub notes (root, for graph wikilinks)"
printf '%s\n' "# Northwind Analytics" "" "Client account. People: [[alex]], [[maya]]." "" "Policies: canary deploys ([[deploy-policy]])." > "$VAULT/Northwind Analytics.md"
printf '%s\n' "# Project Cinder" "" "Postgres 12 to 15 migration. Dashboards: [[atlas]]." "" "Decision: [[ADR - Cinder cutover window]]." > "$VAULT/Project Cinder.md"

echo
echo "seeded notes:"
find "$VAULT" -name '*.md' | wc -l
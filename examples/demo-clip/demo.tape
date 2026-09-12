# Seahorse demo tape — the hero cut (~20s).
#
# Shows the core loop with real CLI output against the disposable vault that
# scripts/render-demo.sh creates (SEAHORSE_VAULT is inherited from its
# environment). Act 2 (the vault note in Obsidian) is a separate OBS capture,
# not part of this tape; the social cut is assembled from this render by
# hand with ffmpeg (see X/launch/2026-09-10-demo-clip-prompt.md, F4).
#
# Style: FontSize 15, 15fps, dark theme, typing at 30ms/char (long commands —
# the 80-120ms human pace would push the hero past 60s). Act 4 is the 1.1
# payoff: a design decision written as a project_doc lands in Memory/ and the
# next session's bootstrap cites it (the original consolidate act stays
# dropped: a fresh vault has no recurrent clusters — unchanged in 1.1).
Output demo.mp4
Output demo.gif

Set Shell bash
Set FontSize 15
Set Width 1200
Set Height 620
Set Theme "Catppuccin Mocha"
Set Framerate 15
Set TypingSpeed 30ms

Hide
Type "export PS1='$ '; clear"
Enter
Wait @5s /\$$/
Show

# Act 1 — the agent remembers, then recalls.
Type "seahorse remember 'Alex Vega joined Northwind Analytics in 2024' --title alex"
Enter
Wait +Screen @30s /Remembered/
Sleep 1s

Type "seahorse recall 'when did Alex Vega join Northwind Analytics?'"
Enter
Wait +Screen @30s /Use `seahorse recall-timeline/
Sleep 800ms

# Capture the fresh ep_id off-screen (demo.sh's grep pattern) — the tape is
# re-rendered against a fresh vault, so the id is never known ahead of time.
Hide
Type "EP=$(seahorse recall 'when did Alex Vega join Northwind Analytics?' | grep -oE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' | head -1)"
Enter
Wait @10s /\$$/
Show

# Act 3 — a correction appends, it does not overwrite.
Type "seahorse improve $EP 'Alex Vega joined Northwind Analytics in 2023' --reason correction"
Enter
Wait +Screen @30s /Improved/
Sleep 800ms

Type "seahorse recall-timeline $EP"
Enter
Wait +Screen @30s /Use `seahorse recall-full/
Sleep 800ms

# Act 4 — the 1.1 payoff: a design decision becomes a durable knowledge note,
# and the next session's bootstrap cites it (verified against a fresh
# disposable vault, 2026-09-12). The hidden `clear` is REQUIRED, not cosmetic:
# once the terminal has scrolled, vhs 0.11 `Wait +Screen` matches the TOP of
# the scrollback buffer instead of the viewport (charmbracelet/vhs#659; fix
# PRs #658/#704 unmerged in v0.11.0, the latest release), so a post-scroll
# wait can never see fresh output. Clearing resets the buffer, and Act 4
# (ADR write + bootstrap cite, ~20 rows) fits one screen without scrolling.
Hide
Type "clear"
Enter
Wait @5s /\$$/
Show
Type "seahorse remember 'Sev-1 alerts page the on-call rota; sev-3 lands in Slack. Ack state syncs to the rota.' --cognitive-type project_doc --title 'ADR: Beacon alert routing'"
Enter
Wait +Screen @30s /Remembered/
Sleep 800ms

Type "seahorse context | tail -8"
Enter
Wait +Screen @30s /Knowledge notes/

# Hold the closing frame before the loop point.
Sleep 2s

# No closing materialize step: with [materialize] configured, remember/improve
# already materialize on write, so after this render the vault holds three
# notes for the Obsidian capture — the old fact (invalid_at set), the
# correction (supersedes frontmatter) and the ADR note (project_doc).
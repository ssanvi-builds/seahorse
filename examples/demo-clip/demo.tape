# Seahorse demo tape — the hero cut (~20s).
#
# Shows the core loop with real CLI output against the disposable vault that
# scripts/render-demo.sh creates (SEAHORSE_VAULT is inherited from its
# environment). Act 2 (the vault note in Obsidian) is a separate OBS capture,
# not part of this tape; the social cut is assembled from this render by
# hand with ffmpeg (see X/launch/2026-09-10-demo-clip-prompt.md, F4).
#
# Style: FontSize 15, 15fps, dark theme, typing at 30ms/char (long commands —
# the 80-120ms human pace would push the hero past 60s). Act 4 (consolidate)
# was dropped from every cut: against a fresh disposable vault it exits 0 with
# "no clusters to distill (0 found)" — never script a step that did not run.
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

# Hold the payoff frame before the loop point.
Sleep 2s

# No closing materialize step: with [materialize] configured, remember/improve
# already materialize on write (verified against v1.0.0), so after this render
# the vault holds both notes — the old one (invalid_at set) and the correction
# (supersedes frontmatter) — ready for the separate Obsidian capture.
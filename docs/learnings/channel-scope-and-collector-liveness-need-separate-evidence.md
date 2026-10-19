# Channel scope and collector liveness need separate evidence

Date: 2026-10-15

## Problem

The workbench combined seventeen bloggers, while an enabled switch suggested monitoring was healthy even when Discord required sign-in. Historical recovery messages used recent ingestion dates and old dry-run executions could be confused with actual paper fills.

## Failed assumptions

Channel names are mutable and are not an authorization boundary. An enabled switch is not a collector heartbeat. A heartbeat alone does not prove complete collection. Marking a message seen before a failed upload prevents later recovery.

## Working approach

Use exact guild and channel IDs at both extension and API boundaries. Preserve prior channels as inactive history. Add collector heartbeats with visible-channel context, show original snowflake time separately from receipt time, and audit changed messages without executing them twice. Regular recent-message scans can recover fresh messages under the existing server age limit; startup recovery never opens historical positions. Only successful acknowledgments enter the content script's seen set.

## General rule

Scope, liveness, completeness, parsing, and execution are separate claims. Show the evidence for each and retain unknown author performance until complete entries, exits and original risk can be reconciled.

## Evidence and related files

`tests/copier/test_focus_monitor.py` verifies exact scope, source revisions and historical parser context. The actual Discord tab required sign-in during this change, so live source-to-ledger reconciliation remains unverified. Files: `tools/discord_copier_extension/`, `yoyo/copier/api/routes/messages.py`, `monitor.py`, and `yoyo/monitor/static/copier.js`.

## Integration follow-through

Chrome was still loading the old archived directory's 0.1.0 bridge. Repository edits therefore did not update the actual collector. The old extension was disabled and the current workspace's 0.4.0 was loaded and reloaded through Chrome; the UI verified the version and enabled state. Discord remained at its sign-in screen. The workbench also serializes filter requests and discards results for a superseded filter (`tests/monitor/frontend_copier.test.cjs`), so stale message rows cannot be presented under a new filter.

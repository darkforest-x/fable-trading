# V1 / V8: observed 0.5R favorable excursion and entry-price protection

One predeclared change: after a completed bar first reaches favorable 0.5 of frozen actual next-open initial R, protect at actual entry from the next bar. Test the already-known stop and adverse gaps first; preserve tighter protection. Keep original admissions, original trailing/exit feeds and 0.20% fixed nominal round-trip cost.

## Strategy identities

1. Native original V1 is long-only and reproducible via `spike_v1_twoyear_allmarkets._trade_rows`, its frozen features/replay outputs and source receipts. Preserve its signal-reference protection contract and every original entry. Compare paired exits only; do not invent a serial account the event ledger never defined.
2. Supplementary V1 common execution uses `v1_common_execution_long` from the 3531 authenticated caches. It shares next-open, 5-bar/0.2ATR/min2ATR initial risk, 2R/4ATR trail and raw V6 reverse exits with the common comparison. It must not be labelled native V1.
3. V8 retains frozen both-direction admission and raw opposite exits. Both fixed-original-entry paired exits and serial replay allowing exit-created reentry are retained for common V1 and V8.

The earlier plan text claiming native V1 could not be replayed was incorrect; this plan supersedes it before evaluation.

## Scope and evidence

- Existing Binance/OKX/Gate 30m/1H/4H data, 2024-09-10 inclusive to 2026-09-10 exclusive UTC, separate years at 2025-09-10. Current listings are not a historical census.
- Owner explicitly permits historical research across all dates. This is one fixed 0.5R configuration, on reused nonblind data, not an untouched holdout or a parameter search. The configuration counter `1` is not a count of physical history reads: native sample/full attempts and common `full_v1`/`full_v2`/`full_v3` replays reread overlapping data after implementation failures. Receipts and the final report preserve those attempts separately.
- Commit builders before evaluation. Baseline receipt and per-trade parity are prerequisites; retain failures explicitly.
- Synthetic checks: stop-before-trigger, adverse gaps, next-bar activation, tighter protection, both sides, precision and causal prefixes.
- Report closed/censored counts, net win rate, mean/sum net R, PF(R), nominal-return PF, original realized >=10R retention, rescued losers/harmed winners and timeframe/year splits.
- R is frozen at actual entry. Price break-even still pays costs; funding and impact are unmodelled. Event cumulative-R drawdown is not account drawdown.
- Fixed original entries control the exit intervention, not entry alpha versus random entries. Cross-venue/timeframe positions are correlated.
- No live orders, Pine, monitoring, notification, ACTIVE or paused automation changes.

## Ownership

- Parent: this plan, shared registries, integrated report/HTML and Notion note.
- Terra High `v1_v8_be05`: common V1/V8 replay and `common/` results.
- Terra High `native_v1_be05`: original V1 replay and `native_v1/` results.

## Execution correction discovered during parity (2026-09-14)

Initial diagnosis incorrectly attributed a stale exit intent to the archived V8 engine. Source inspection and an 86-event failing-stream trace corrected that diagnosis: `spike_exit_policy_study.replay_policy` already clears `pending_exit` on an opening-gap stop. The bug was introduced in this study's custom replay, whose added opening-gap branch failed to clear `pending_reverse`. Reproducing this study's buggy serial output in fixed-entry replay merely to obtain internal parity was not acceptable. This correction is recorded explicitly rather than rewriting the failure as an archive defect.

The revised evidence sequence retains an independently replayed archival baseline for exact oracle parity, then audits its differences, if any, from this study's corrected baseline. Only clean baseline and clean 0.5R-BE are compared. Both arms use the same corrected engine; no signal parameters, costs or risk widths change. Save archive-to-clean event/count/R differences and both clean serial and fixed-entry outcomes. Previously generated `full_v1` and `full_v2` outputs remain historical failed/interrupted attempts and cannot be presented as final evidence. The affected 86-event stream now matches the archive; full-scope audit is required before claiming zero archive differences.

This is an explicit correction of simulator state ownership, not a selected profitable parameter. Native V1 uses its own protection loop and remains a separate experiment arm. No shared historical builder, live execution or notification behavior is changed by this study.

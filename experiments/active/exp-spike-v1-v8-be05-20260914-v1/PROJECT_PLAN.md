# V1 / V8: observed 0.5R favorable excursion and entry-price protection

One predeclared change: after a completed bar first reaches favorable 0.5 of frozen actual next-open initial R, protect at actual entry from the next bar. Test the already-known stop and adverse gaps first; preserve tighter protection. Keep original admissions, original trailing/exit feeds and 0.20% fixed nominal round-trip cost.

## Strategy identities

1. Native original V1 is long-only and reproducible via `spike_v1_twoyear_allmarkets._trade_rows`, its frozen features/replay outputs and source receipts. Preserve its signal-reference protection contract and every original entry. Compare paired exits only; do not invent a serial account the event ledger never defined.
2. Supplementary V1 common execution uses `v1_common_execution_long` from the 3531 authenticated caches. It shares next-open, 5-bar/0.2ATR/min2ATR initial risk, 2R/4ATR trail and raw V6 reverse exits with the common comparison. It must not be labelled native V1.
3. V8 retains frozen both-direction admission and raw opposite exits. Both fixed-original-entry paired exits and serial replay allowing exit-created reentry are retained for common V1 and V8.

The earlier plan text claiming native V1 could not be replayed was incorrect; this plan supersedes it before evaluation.

## Scope and evidence

- Existing Binance/OKX/Gate 30m/1H/4H data, 2024-09-10 inclusive to 2026-09-10 exclusive UTC, separate years at 2025-09-10. Current listings are not a historical census.
- Owner explicitly permits historical research across all dates. Fixed-configuration holdout-era use 1; reused nonblind data, no parameter search.
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

The archived V8 replay uses `spike_exit_policy_study.replay_policy`. Its separate opening-gap stop can close a position before the pending-reverse cleanup block, leaving an old position's exit intent available to a later entry. A fixed-entry replay exposed the discrepancy. Reproducing that stale intent merely to match the archive is not an acceptable new executable baseline.

The revised evidence sequence retains a legacy baseline solely for exact archive parity, then measures the change to a corrected baseline which clears exit intent when its owning position ends. Only after this separately identified correction are clean baseline and clean 0.5R-BE compared. Both BE arms use the same corrected engine; no signal parameters, costs or risk widths change. Save legacy-to-clean event/count/R differences and both clean serial and fixed-entry outcomes. Previously generated `full_v1` and `full_v2` outputs remain historical failed/interrupted attempts and cannot be presented as final evidence.

This is an explicit correction of simulator state ownership, not a selected profitable parameter. Native V1 uses its own protection loop and remains a separate experiment arm. No shared historical builder, live execution or notification behavior is changed by this study.

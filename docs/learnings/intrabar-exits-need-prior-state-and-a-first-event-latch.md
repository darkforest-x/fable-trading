# Intrabar exits need prior EMA state and a first-event latch

2026-11-03 · Owner changed the cloud-reference exit from confirmed12H reversal to the first intrabar reversal. This is indicator/reference accounting, not executed P&L.

## Problem

BTC Apr8 20:00 Beijing confirms Apr9 08:00 with EMA20 below EMA50, but the next12H open already makes the provisional EMA bullish. A close-only exit retains that entire bar and reports1.11R; the new rule exits at its open71041.8 with a descriptive0.532659R.

## What did not work

- Comparing only finalized EMA values postpones the exit until12H close.
- Recalculating MFE from an expanding forming candle after its first exit admits post-exit extremes.
- A Pine `varip array<Plan>` does not make ordinary UDT fields rollback-proof. The first-event payload also needs explicit `varip` fields.
- A path feed tied only to the displayed12H chart becomes stale when the user views5m.

## Effective approach

Use previous confirmed12H EMA values and provisional price P: fast=(1−2/21)×prevFast+(2/21)×P; slow=(1−2/51)×prevSlow+(2/51)×P. Solve their equal-price threshold; test the open first, then adverse OHLC extremes. When both adverse barriers are touched, the nearer threshold wins; an opening gap through both uses fixed-stop precedence.

Opening reversal ends exactly at the open and excludes all later extrema. Non-opening historical OHLC exits retain a12H time interval and MFE bounds, rather than inventing intrabar timing or wick order. Lock every observed live plan's first exit and MFE bounds across subsequent ticks, close confirmation and display-period changes. Stop-setting changes intentionally invalidate those locks.

The website keeps an independent native12H forming-candle feed when other periods are visible. Pine uses a dedicated `ExitLatch` whose scalar fields are all `varip`; ordinary historical `Plan` state retains normal bar commits.

## General rule and evidence

Signal confirmation time, intrabar exit time and rendering period are separate contracts. Test each with opening gaps, strict equality, same-bar competing barriers, live→closed updates, later favorable extremes and period switches.

- Implementation: `yoyo/evaluation/pine/spike_signal_cloud_v1_6.pine`, `yoyo/monitor/static/cloud-signal-chart.js`.
- Regression: `tests/monitor/frontend_cloud_signals.test.cjs`; evidence `output/cloud-intrabar-exit-20261103/`.
- Pine rollback semantics: https://www.tradingview.com/pine-script-docs/language/objects/

Limits: no live market crossing was induced or awaited. Historical OHLC cannot reproduce exact first observed tick time after reload; stored bounds must not be represented as realized profits.

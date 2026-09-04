# TradingView clean display needs both layout and episode de-duplication

## Problem

The BTCUSDT.P 15m chart showed three moving-average curves, many overlapping
K1/K2 tags, and overlapping reward/risk boxes after the high-recall research
indicator was saved to TradingView. The chart looked different depending on
whether the indicator was selected, which made the apparent style regression
harder to diagnose from a screenshot alone.

## Root cause

There were three independent causes:

1. The chart layout had both `Fable K1→K2 V2` and `Fable 15m Trend V2`
   enabled. The former drew SMA40 and its own events; the latter drew EMA30,
   SMA60, and a second event stream.
2. Trend V2 treated every de-duplicated `direct`, `rejection`, or `coil` raw
   state transition as a display signal. A three-false-bar reset is candidate
   de-duplication, not a K1→K2 episode state machine. It therefore continued
   drawing K1/K2 tags while a position was already active.
3. TradingView preserves style state when the source of an existing indicator
   is replaced. New source defaults such as `SMA60 · runner = false` did not
   automatically replace the old instance's checked style switches. The
   instance had to be reset to the script defaults after a successful compile.

## Fix

- Remove the older Fable indicator from the layout instead of layering a new
  research script on top of it.
- Emit a visual event only after a body-crossing K1 is paired with a physical
  EMA-touch K2 two to eight bars later. The K2 body must remain on the trend
  side, and intermediate closes may not materially return to the wrong side.
- Use the one-position state as the display lock, so raw candidates cannot add
  labels or position boxes while a trade is active.
- Keep only EMA30 visible by default. SMA60 remains available internally for
  the trend runner and can be enabled manually for diagnosis.
- Keep source-style candle body, border, and wick colours; highlight only the
  accepted K1/K2 candles; disable the redundant L/S marker and status chip by
  default.
- After replacing source, use TradingView's **Reset settings** action before
  judging the result, then click empty chart space to verify the unselected
  appearance.

## Verification

- TradingView Pine v6 compiled the new source without an error on 2026-09-04.
- The saved cloud script is `Fable 15m K1→K2 Episode V3`.
- The saved `综合过滤` layout contains one Fable indicator, not two.
- With the indicator unselected, the inspected two-day view showed one
  full-length EMA curve and three accepted K1/K2 pairs instead of the previous
  dense raw-state labels.
- This was a display and causal-state correction. It did not score economic
  returns and read zero repository holdout price rows.

## General rule

A clean indicator needs two separate guarantees: **one active script in the
layout** and **one accepted event per semantic episode in the source**. Fixing
only one still leaves either duplicate plots or duplicate signals. When a Pine
script is replaced in-place, always audit and reset the persisted instance
styles before comparing screenshots.

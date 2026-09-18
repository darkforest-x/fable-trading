# A hand-drawn trendline the engine missed: measure every rule on that exact triple

- **Problem**: owner 2026-09-19, SOL and ETH 15m: an obvious descending line (hand-drawn in white) never appears in V10.4/V11.2.
- **Dead ends**: the first answer listed possible causes (only one displayed main line, strict pivots,
  body crossing, search happens only on a new pivot). All plausible, none checked; the owner still could
  not tell which one applied. Holding out for data that had been available the whole time wasted a day.
  Looking at the TV screenshot was also misleading: its "C" and "三点确认" labels belonged to the **1h**
  higher-timeframe line (confirmation 8 h after C = right=8 at 1h), not to a 15m line.
- **What worked**: fetch the exact bars, name the owner's A/B/C by timestamp, and push that single
  triple through every engine rule, recording the measured value against its threshold
  (`yoyo/evaluation/spike_line_diagnose.py`), plus a whole-file funnel.
  - SOL: A 09-15 04:30 ties the next bar's high (104.83 = 104.83), so under strict `ta.pivothigh`
    neither bar is a pivot; B→C is 19 bars (min 24); C sits 0.58 ATR off the line (max 0.35).
    Only when all three are relaxed together does the owner's line appear (confirmed 09-16 04:00, broken 17:45).
  - ETH: the owner's middle touch (09-15 21:00, 2486.66) is not a pivot, because 19:00 is 1.66 higher and
    8 bars earlier; the real pivot sits 11 below the owner's line.
  - Funnel (45 days, 15m): "C within 0.35 ATR" keeps 6% and "no body crossing A→now" keeps 4%;
    SOL got 9 lines in 45 days. The engine draws far fewer lines than a person does, by design.
- **General rule**: when a person says "this obvious line / signal is missing", do not list hypotheses.
  Pin the exact bars first and report each rule's measured value on that instance. Then, for a
  "how often" answer, run the same rules over the whole file as a funnel.
- **Related**: `spike_v10_4.py` (`_search`, `_validate`, `pivots` tie rule), `V104Params` defaults
  (min_gap 24, touch 0.35, body_tol 0.15). Changing them is the owner's call; see
  [undocumented-pivot-tie-rule-measure-the-one-sided-extra-not-loose-ties.md](undocumented-pivot-tie-rule-measure-the-one-sided-extra-not-loose-ties.md).

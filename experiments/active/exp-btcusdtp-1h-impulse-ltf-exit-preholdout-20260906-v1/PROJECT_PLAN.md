# Hourly impulse and lower-timeframe exits

Owner authorised this new rule family and parameter exploration on 2026-09-06.
The initial entry/exit architecture is the explicitly requested combined change.
Subsequent comparisons vary one entry coordinate or one exit policy at a time.

1. Implement native OHLC engulfing and completed-hour body crossing; reproduce
   MA Shift colour independently as HL2 versus its native-timeframe moving average.
2. Verify 5m execution and 15m confirmation timing, stop-first fills, true partial
   realisations, gap censoring and full horizon at every chronological boundary.
3. Commit implementation and config before computing price outcomes.
4. Develop only in 2023-2024, with every fold's last 72 hours excluded from entry.
   Compare eight exits on identical hourly signals, then one finite entry-coordinate
   pass. Record every failure and match random controls before judging any final result.
   The 1h colour and fixed-3R exits are diagnostic controls, not eligible finalists:
   the requested new family must retain lower-timeframe management. Match control
   timestamps without reuse and test differences by calendar matching month.
   Both week- and month-cluster inference remain approximate for outcomes crossing
   a calendar boundary; passing them does not replace prospective evaluation.
5. Freeze and commit selection before scoring 2025-February 2026 transport audit.
   The years are not globally pristine because prior unrelated research used them.
6. Produce a trade ledger, direction/fold/cost/tail/failure diagnostics, executable
   rules, a Chinese Markdown and HTML report, and source-bound charts.
7. A positive transport audit is a candidate for a separate prospective evaluation;
   it is not permission to trade or a guaranteed profitability claim.

No model training or existing Pine/deployment mutation is part of this experiment.
No repository holdout source is required for this development and transport run.

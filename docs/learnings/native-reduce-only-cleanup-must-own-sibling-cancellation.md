# Let native position closure cancel its reduce-only sibling orders

- **Problem**: Adding resting fixed profit targets to the Nautilus adapter caused
  cancellation rejections after either TP or SL filled, although there was only
  one closing fill and the sibling had previously been accepted.
- **Dead end**: Sending a sibling cancellation from the fill callback races
  Nautilus1.231.0's own reduce-only cleanup: the engine cancels the sibling after
  dispatching that fill, before the callback's queued cancellation is consumed.
  Ignoring all cancellation rejections would conceal real execution errors.
- **Effective path**: Inspect installed `backtest/engine.pyx` reduce-only cleanup.
  Let native closure cancel the sibling, keep explicit cancellation before a
  boundary market exit, and require no open native orders after all positions
  close. Tests cover both sides, gaps, dual-touch bars and later bars that would
  otherwise fill an orphan. Native resting targets fill at their limit on
  favorable gaps; a screening shortcut that grants the opening price mismatches.
- **General rule**: Before implementing bracket lifecycle manually, identify who
  owns cancellation and when callbacks run. Match the installed engine, preserve
  its errors and verify orders as well as positions. OHLC-based dual-touch order
  is a modelling assumption, not observed tick evidence.
- **Connections**: `yoyo/layers/l3_backtest/nautilus_independent.py`,
  `yoyo/evaluation/pepe_range_be_native.cpp`,
  `tests/evaluation/test_range_exit_search.py`,
  `experiments/active/exp-eth-exit-search-20261020-v1/PROJECT_PLAN.md`;
  https://nautilustrader.io/docs/latest/concepts/backtesting/bar-execution/.

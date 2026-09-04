# BTCUSDT.P 15m trend-regime episode V4

This experiment repairs a semantic error in the TradingView Episode V3
display: a position exit currently rearms the K1/K2 detector even when the
market has not entered a new trend regime.

V4 separates three states:

1. `neutral`: no directional fast/slow MA regime is established;
2. `armed`: a causal EMA30/SMA60 trend regime exists and has not emitted a
   trade;
3. `consumed`: the regime already emitted its only K1/K2 trade.  Stop or time
   exit does not change this state.

The numeric spread, slope and neutral-dwell thresholds are selected in that
order, one factor at a time.  The repository holdout beginning 2026-05-04 is
physically absent from the economic source and may not be read by this study.

This remains a research display.  It cannot alter ACTIVE/frozen, forward,
deployment, sizing, API keys, or live orders.

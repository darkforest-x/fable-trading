# A timeframe comparison must parameterize every decision clock

- **Problem**: Testing the 15m wick strategy on5m candles must change signal matching, Monday-open timestamps, entry-key snapshots and pivot confirmation together. The native pivot adapter had a fixed15m resample and45minute center-open offset.
- **Dead end to avoid**: Merely passing5mOHLC to an admission function or setting management_minutes=5 leaves other clocks unchanged. It can label signals ten minutes early, reset Mondays incorrectly, or silently run5m entries with15m exits.
- **Effective path**: Add explicit bar_minutes and pivot_minutes with unchanged15m defaults. Derive candle-open and pivot-center timestamps from those values. Verify NYMonday boundaries, entry-close snapshots, two-bar confirmations, stop precedence and prefix causality; reproduce the frozen75BTC/69ETH15m admissions before running5m.
- **General rule**: Inventory every hardcoded aggregation and timestamp offset before comparing resolutions. Preserve bar-count versus wall-time semantics explicitly; pre5 and +/-2 are counts here, so their durations change.
- **Scope**: V2.31 admission oracle, Monday grid, entry_keys, Nautilus confirmed_pivots, and the5m study. No Pine/default/live change. Same20bp cost and native5m execution substrate; smaller risk distances magnify cost in R.

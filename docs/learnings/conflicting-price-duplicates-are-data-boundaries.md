# Conflicting price duplicates are data boundaries

- **Problem**: Long HistData XAUUSD archives contain repeated timestamps with different OHLC. The normal loader correctly rejects them, so a long backtest cannot treat the source as unique.
- **Dead end**: Keeping the first or last row would invent an ordering and could choose a stop or target outcome. Removing the duplicate alone still lets positions bridge the missing interval.
- **Effective path**: Exclude the entire affected five-minute interval, split replay at each excluded interval, and censor positions before it. Reject Monday anchors containing conflicts; reset causal warmup after each boundary. Preserve the exact excluded timestamps and raw source archives.
- **General rule**: Source ambiguity changes what can be evaluated. It must become an explicit boundary in the execution engine, not just a cleanup in the input table.
- **Related**: `yoyo/evaluation/monday_5m_wick_gold.py`; original input remains unchanged; spot Bid-only costs remain unknown.

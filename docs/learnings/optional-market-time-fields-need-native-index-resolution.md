# Resolve optional market timestamps from verified native fill indices

- **Problem**: The native adapter returns actual fill indices, but a market row can retain a missing caller-supplied `entry_time`. Treating that field as authoritative left account events unresolved.
- **Dead end**: Parsing a missing timestamp as an integer silently puts the fill before every signal; guessing a later timestamp would change the strategy.
- **Effective path**: Verify each market fill index equals its completed decision index, then assign that observed decision close. Limit entries still require the native fill time. Fail on unresolved order events and retain the failed summary log; no candles or completed executions need replaying.
- **General rule**: Timestamp presence and timestamp meaning are separate contracts. Assert the event's native index before filling an optional display field.
- **Connections**: `yoyo/evaluation/allin3_close_hybrid_summary.py`, `experiments/active/exp-allin3-close-hybrid-20261029-v1/summary-first-missing-time-failure.log`.

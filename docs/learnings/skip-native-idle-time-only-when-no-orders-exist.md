# Skip native idle time only when no orders or positions exist

- **Problem**: Sparse weekly signals spend most native replay time processing bars before any order exists.
- **Dead end**: Cropping at a predicted exit or inferring future profitability to shorten a replay would make the execution envelope depend on future prices. Replaying unchanged market entries again would also waste work.
- **Effective path**: Reuse identical saved native fills. For new reclaim orders, skip only when both strategy state and the native order cache are empty, and resume at the next known submission close. Dense versus skipped native fixtures have identical fills, trades and opportunities under both declared OHLC paths.
- **General rule**: Optimize a replay using current lifecycle state and scheduled decisions, never a guessed future fill or exit. Pending orders remain economically active even when no position exists.
- **Connections**: `yoyo/layers/l3_backtest/nautilus_independent.py`, `tests/evaluation/test_allin3_close_hybrid.py`, `yoyo/evaluation/allin3_close_hybrid.py`. Applies only to streamed reclaim management with precomputed causal inputs.

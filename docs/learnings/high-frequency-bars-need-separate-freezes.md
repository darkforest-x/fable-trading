# High-frequency bars require separate frozen artifacts

- **问题**：The owner requested 2m and 3m research to increase opportunity coverage, while the active judgment model and threshold were frozen on 15m candidates.
- **死胡同**：Treating a bar label as a transport-only change would reuse the 15m model on features whose wall-clock windows, candidate frequency and outcome horizon changed materially. That creates more scores but no valid comparison.
- **有效路径**：First prove the exchange and local data pipeline support each bar, then isolate 2m and 3m as separate chronological experiments with their own dataset, purge, model and freeze. Keep fixed-cost reporting and never write their results into 15m books.
- **通用规则**：A timeframe change is a distribution and semantics change, not an inference option. Add data support once, but train, validate and freeze every timeframe independently.
- **牵连**：`src/data/bars.py`, `src/data/update_okx.py`, pre-holdout split, SMA/EMA 20/60/120 windows, cost sensitivity, ACTIVE and forward-ledger isolation.

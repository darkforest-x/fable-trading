# Preserve replay history while trimming display materialization

- **问题**：V1 cold scan retained 720 bars for causal features and replay, but then constructed 720 JSON chart rows per cell before the worker discarded all but the final 240.
- **死胡同**：Reducing the input candle history would alter recursive moving averages and replay state, so it would trade runtime for a different signal protocol.
- **有效路径**：Keep the full frame, features and replay unchanged; add a display-only `chart_limit` that skips JSON row construction before the final chart window. Equality tests compare full versus limited state, events and every retained chart row.
- **通用规则**：When a causal engine needs long history but its API retains a short tail, optimize after the causal calculation and prove retained outputs byte-for-value equal. Do not shrink the causal input window to solve a display allocation cost.
- **牵连**：`yoyo/monitor/signals.py`、`yoyo/monitor/v1_worker.py`、`tests/monitor/test_v1_chart_limit.py`；this does not change Pine, V1 feature windows, replay, risk, notifications, or fetch concurrency.

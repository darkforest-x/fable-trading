# Chart level prices can identify a missing venue

- **问题**：The owner's cropped BTCUSDT.P chart omitted the exchange. A provisional Binance interpretation produced a valid but different venue backtest.
- **死胡同**：The .P suffix identifies a perpetual product, not a unique exchange. A default symbol page is insufficient evidence for the source of a cropped chart.
- **有效路径**：Compare both completed Monday extrema at the indicator's timezone against registered datasets. Screenshot H83149.8/L81166 matches OKX2026-10-19 exactly; Binance H83157.9/L81146.4 does not. Preserve and label the Binance result, then calculate the chart-matched OKX result.
- **通用规则**：For screenshot-based research, verify visible price anchors against the proposed venue before treating its result as the owner's exact strategy. Similar prices are not source identity.
- **牵连**：`experiments/active/exp-btc-monday-fib1618-20261022-v1/`; `experiments/active/exp-btc-monday-fib1618-okx-20261022-v1/`; NY Monday boundaries and immutable prior results.

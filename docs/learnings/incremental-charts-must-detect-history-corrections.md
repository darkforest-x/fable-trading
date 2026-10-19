# Incremental charts must detect corrected history

- **问题**：Repeated full series updates and per-pointer array copies add work as chart history grows.
- **死胡同**：Comparing only the last timestamp and close misses revised historical candles and moving averages.
- **有效路径**：Compare complete points at data-refresh time; use incremental updates only when the unchanged prefix permits it. Index timestamps once and coalesce pointer work into one frame.
- **通用规则**：Optimize by event frequency: verify history on data changes, then use constant-time lookup during interaction. Never replace six source averages with cheaper approximations.
- **牵连**：workspace-runtime.js, market-terminal.js, yolo-detect.js; frontend_performance.test.cjs

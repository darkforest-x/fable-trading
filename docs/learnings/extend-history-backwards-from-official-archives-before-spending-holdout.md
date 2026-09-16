# "回测再长一点"先往前接官方档案，不要往后动 holdout

- **问题**：Owner 说"长一点看看"。本地 5m 只有 2025-12-20 起的 4.4 个月可用段，
  文件里后面那截（2026-05-04 之后）是 holdout，读它要逐次授权且不可退款。
- **死胡同**：把窗口往后推到 holdout。那是最贵的方向，而且拿到的是最短的增量（本例仅 2 个多月）。
- **有效路径**：OKX 官方月度 1m 档案（`static.okx.com/cdn/.../candlesticks/monthly`）可以往前接，
  `src.data.fetch_okx` 的 archive 模式聚合成 5m 并强制 `--archive-max-exclusive` 边界。
  28 个月、245,088 根，holdout 消耗 0；而且和此前独立抓取的 API CSV 在 37,800 根重叠上
  **OHLC 逐值一致**，等于顺手做了一次双源互证。
- **通用规则**：要更多数据先问"能不能往前"，把 holdout 留到真正的终审。往前接来的历史也不是免费的：
  **看过一次就等于花掉**，之后拿它调参它就不再是未接触数据——所以接完当轮只跑事前冻结的组，
  不做参数搜索，并在报告里写明这段已被消费。
- **牵连**：`src/data/fetch_okx.py` archive 模式、`data/kline_deep_5m/`、`docs/HOLDOUT_LEDGER.md`。
  月度档案按 UTC+8 日历切分，月文件首根是上月最后一天 16:00 UTC，拼接时要按 ts 去重。

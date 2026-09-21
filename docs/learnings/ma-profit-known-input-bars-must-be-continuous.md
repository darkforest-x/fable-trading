# MA-profit causal inputs require timestamp continuity, not only row counts

- **问题**：3m archive sources contain internal timestamp gaps. Row-count warmup checks and core-only continuity could admit a rendered MA input whose 1,200-bar support or pre-core context silently skipped market time.
- **死胡同**：只检查核心 4–5 根 K 线连续，或只检查 1,200 根行数，都把“缺一根后仍有足够行数”误当成连续可见历史；补一根合成 K 线又会伪造市场数据。
- **有效路径**：在标签 split 与资产渲染两处检查同一已知区间：`core_start_i - 11 - 1200` 到 `core_end_i + 5` 的相邻 UTC open_time 必须恰好相隔 bar_minutes。标签将失败事件记为 `purged/known_input_gap`；已冻结而进入渲染的事件直接拒绝。
- **通用规则**：任何依赖固定 bar 窗口的因果特征，都要同时验证窗口的时间连续性；行数、核心局部连续性和未来标签窗口不能替代该检查。
- **牵连**：`yoyo/datasets/ma_profit_pipeline.py`、`yoyo/datasets/ma_profit_dataset.py`、`tests/evaluation/test_ma_profit_input_continuity.py`；检查只覆盖已知 c+5 输入，不改变收益 resolver、障碍参数或未来标签处理。

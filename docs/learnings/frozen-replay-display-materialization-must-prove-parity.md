# 冻结回放的显示物化优化必须先证明 parity

- **问题**：V1 scanner 的每单元完整 720 根回放在资源紧张时很慢。采样命中 pandas Series `.iloc`、`iterrows()` 与 NumPy 属性读取；直接缩短历史或并行重放都会危及冻结 recurrence、warmup 或系统稳定性。
- **死胡同**：把 chart 缩至 240 根只能减少最后持久化的 JSON，不能避免 replay 后逐根用 pandas 行级访问的成本；在 swap 已接近耗尽时增加进程并发也只会放大调度竞争。
- **有效路径**：完整 `features` 与 `replay` 保持原样执行，再一次取得已计算列的数组用于 event/chart 物化。用变更前实现对固定 720 根输入做完整输出 equality，另固定 SHA；基准只报告离线函数时间，运行态再通过分阶段 timing 验证。
- **通用规则**：冻结时序函数出现显示层热点时，先区分“计算结果”与“结果物化”。只优化后者也必须对 state、events 与展示窗口建立输入相同的 parity 证据，不能用缩短输入替代证据。
- **牵连**：`yoyo/monitor/signals.py`、`yoyo/monitor/v1_worker.py`、`tests/monitor/test_v1_chart_limit.py`、`tests/monitor/test_v1_worker_cache.py`；不改变 Pine、V1 特征、风险、Bark cutover 或持久 OHLC checkpoint。

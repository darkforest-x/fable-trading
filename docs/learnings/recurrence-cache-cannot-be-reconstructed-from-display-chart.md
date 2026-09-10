# 因果递推缓存不能从展示图恢复

- **问题**：V1 scanner 重启后会丢失内存中的完整 OHLC 序列；SQLite `markets.chart` 只保留 240 根展示数据，而 V1 预热至少需要 340 根，裁剪后再接增量会改变递推状态。
- **死胡同**：把 UI chart、已有数据目录或 scan 进度当作 restart seed 都缺少完整连续原始序列；它们既不能证明无 gap，也不保留旧进程的 recurrence origin。
- **有效路径**：scanner 仅把完整 raw OHLCV 序列压缩存入 monitor 私有 SQLite checkpoint。新进程严格验证每根的周期对齐、连续性、整型时间戳、有限 OHLCV 和价格边界；任何坏 payload 直接丢弃并冷取。恢复后仍调用 `OKX.candles(previous)`，只由交易所增量合并。
- **通用规则**：对递推特征，持久化的是输入序列而不是派生 UI 视图；恢复测试必须覆盖一次 restart 后的增量 state/chart 等价与 gap 的 fail-closed。
- **牵连**：`yoyo/monitor/store.py`、`yoyo/monitor/v1_worker.py`、`tests/monitor/test_v1_worker_cache.py`；checkpoint 仅用于本机 monitor，不写 VPS OHLCV cache。

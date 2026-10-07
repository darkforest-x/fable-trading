# 冒烟测试监控适配器，要用调用方真实的窗口长度和合约元数据

- **问题**：把 V13 回踩监控从 15m 扩到 15m/30m/1H/4H 后，想先在本地缓存的真实 K 线上冒烟验证。第一次 50 个合约 × 4 周期，确认数为 0。第二次用真实 tick，每周期也只有 1 个原始候选。
- **死胡同**：① 第一次随手给了 tick=1e-6、base_asset=None。② 第二次照抄扫描器 `client.candles(limit=720)`，把检查点截到最后 720 根。V12.8 有 520 根预热，720 根只剩约 200 根可判定，加上检查点的起点不同，原始候选几乎为零。差点把“候选为 0”当成 V13.1 代码的问题去改。
- **有效路径**：拿线上扫描器自己写进 markets 表的状态（`available_bars` ≈ 1272、`tick_size`、`base_asset`）重放同一批合约。原始候选数与线上状态里的事件数对上（15m 165 对 143，差额是线上按启用时间截断），V13.1 在四个周期都有确认，1H 追踪是 2ATR、其他周期 4ATR。
- **通用规则**：冒烟测试的第一步，是先复现调用方的输入：窗口长度、预热、tick、元数据，都从线上状态里读，不要照抄 fetch 的 limit 参数。先拿上游计数和线上对账，对上了再看下游结果。下游为 0 时，先查上游是否本来就是 0。
- **牵连**：`yoyo/monitor/v9_worker.py`（扫描循环与累积 K 线）、`yoyo/monitor/v128_signals.py`（WARMUP）、`yoyo/monitor/v130_signals.py`；另外 `tests/monitor/test_api_startup_imports.py` 会拦住在 `service.py` 顶层 import numpy 的改动（共享常量放进轻量的 `v130_policy.py`）。

# Thread CPU timing separates scanner wait from frozen replay compute

- **问题**：扫描 wall time 把 public fetch、SQLite 等待和主线程 Pandas/Pine replay 混为一个数字，孤立命令的快慢不能解释常驻 worker 的慢 pass。
- **死胡同**：只累加 wall clock，或用单次 CLI 运行推断 daemon 计算成本；两者都无法说明主线程实际占用 CPU 的比例。
- **有效路径**：保留原有每 cell wall 计时，并在 scanner 主线程的 `analyze` 与 checkpoint 调用两侧记录 `time.thread_time()` 的 total/max；同一 pass 另报有效 candle 的 changed/unchanged 数。空数据或 fetch 异常不冒充 changed cell。
- **通用规则**：性能诊断先分离 wall、线程 CPU 和有效工作量，再讨论调度或数值优化。诊断字段不能改变 candle seed、replay、事件、出站策略或 cell 顺序。
- **牵连**：`yoyo/monitor/v1_worker.py` 的 scan meta `timing_ms`；`tests/monitor/test_v1_worker_cache.py` 用固定 synthetic outputs 验证 chart/event/outbox 与 changed/unchanged 指标。

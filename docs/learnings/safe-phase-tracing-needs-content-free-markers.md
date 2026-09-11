# 分段性能 trace 只能记录内容无关的边界

- **问题**：历史信号 cursor 页偶发超时；小页 SQLite 读取很快，旧记录也无法区分调度、读取、freshness 循环和 FastAPI 序列化。
- **死胡同**：用单个 limit=1 请求、SQL `EXPLAIN` 或缩小页面推断整条 HTTP 路径，会遗漏响应编码和解释器被其他同步请求占用的时间；记录 query、symbol 或 event id 虽方便排查，却会把用户筛选和历史身份写入运行日志。
- **有效路径**：在既有无 query 的 dispatch trace 内，为 `/api/signals` 依次记录 handler 到达、数据库行数、freshness 完成和 handler 返回的单调时间；middleware 的 exit 保留为编码/返回后的外沿。标记只含固定阶段名和行数。
- **通用规则**：对受限本机 API 的性能问题，先以内容无关的阶段时钟确定瓶颈边界，再改变 SQL、页大小或超时；一次受控复现后关闭 trace。
- **牵连**：`yoyo/monitor/server.py`、`tests/monitor/test_spike_v1_api_replay.py`；不触及 V1、OHLC、cutover、风险或通知。

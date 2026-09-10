# 监控健康探针不能聚合历史 payload

- **问题**：SPIKE 服务仍能监听 8766，但 `/api/health` 先调用完整 `status()`；后者读取市场图表、事件 JSON 和通知回执。9,000 多条历史事件时，一个本应只回答 liveness 的探针耗时数秒，反过来让调用方把存活服务误判为不可用。
- **死胡同**：把 health 当作完整仪表盘的别名。即便各个状态字段本身真实，健康检查只需要最新扫描元数据和模型就绪状态；读取历史图表或回执既不增加 liveness 证据，也会让 SQLite 解码和扫描成为探针瓶颈。
- **有效路径**：单独读取 `scan` 元数据、校准时钟和模型 gate 状态计算 health；状态页的市场计数改为 SQL 聚合，通知计数从 outbox 出发，避免无待发回执时扫描事件。完整市场/图表仍由专用 API 按需读取。
- **通用规则**：健康端点只能依赖判断健康所需的最小、可索引状态。若状态页需要历史聚合，拆成摘要查询或按需端点，不能复用给 liveness。
- **牵连**：`yoyo/monitor/server.py`、`yoyo/monitor/service.py`、`yoyo/monitor/store.py`、`tests/monitor/test_spike_v1_health.py`；不改变 SPIKE V1 信号、价格、通知资格或任何收益计算。

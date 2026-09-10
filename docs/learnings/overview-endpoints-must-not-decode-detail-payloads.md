# Overview endpoints must not decode detail payloads

- **问题**：服务已把状态读取移出 HTTP 处理器，`/api/status` 仍会在扫描期间出现长时间无首字节。
- **死胡同**：继续压缩状态快照不会消除阻塞；trace 显示 status handler 本身只运行约 16ms，排除了该路由的业务读取。
- **有效路径**：同一 trace 中 `/api/markets` 解码并序列化了 214 个 market 的约 11.5MB `chart/events` JSON，持有解释器执行权。市场列表只读取 SQL `json_remove` 后的摘要；选择一张图时才用主键读取完整 payload。
- **通用规则**：有完整图表或事件阵列的持久化行，概览接口必须投影摘要；详情接口再按唯一标识读取完整数据。不要把概览响应当作详情缓存。
- **牵连**：`yoyo/monitor/store.py`、`yoyo/monitor/service.py`、`yoyo/monitor/server.py`、`tests/monitor/test_market_summaries.py`；API 字段的完整图仍由 `/api/chart` 提供。

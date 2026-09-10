# 市场概览必须与图表 payload 分开持久化

- **问题**：`/api/markets` 用 SQLite `json_remove` 从每个市场完整 payload 去掉 `chart` 和 `events`，但 SQLite 仍需解析约 95 MB 的源 JSON；旧页面的并发轮询会把这些同步请求堆在 API 进程中。
- **死胡同**：缩小响应字段或给 JSON 表达式加索引都没有避免读取和解析完整 payload，因此不能解除 GIL 与请求队列阻塞。
- **有效路径**：市场行写入时在同一事务保存不含图表和事件的紧凑摘要；旧行只在扫描子进程启动时回填一次。HTTP 概览只读摘要表，信号页不请求概览，观察页在用户进入时单次懒加载。
- **通用规则**：只要面向概览的字段来自大 JSON 文档，就先验证数据库是否仍解析完整文档；若是，写入时物化受控摘要，不能把 JSON 投影当作性能隔离。
- **牵连**：`yoyo/monitor/store.py`、`yoyo/monitor/v1_worker.py`、`yoyo/monitor/static/app.js`；不改变 V1、风险、cutover 或通知规则。

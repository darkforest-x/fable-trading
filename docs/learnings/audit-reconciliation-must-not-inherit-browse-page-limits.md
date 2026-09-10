# 审计重链不能继承浏览页数上限

- **问题**：回放账本联结复用了 `list_events(limit=2000)`。导入 6,185 条后，命令静默只重链了第一页，receipt 也只写了该页。
- **死胡同**：把 UI 提升到更大 limit 既不能保证全量，也会让审计逻辑依赖浏览器的资源预算。
- **有效路径**：重链、失效和 receipt 写入都以 `(bar_close_ms,event_id)` cursor 遍历完整 replay corpus；失败的 tuple/source/OHLC 检查也写入逐条 receipt，并且不携带 outcome。
- **通用规则**：API 的分页边界是交互契约，不是审计范围。任何批量核验必须显式遍历所有 cursor page，并把未通过原因与成功行一起记录。
- **牵连**：`yoyo/monitor/replay_ledger.py`、`tests/monitor/test_replay_ledger.py`、`static/app.js`；缺 OHLC 不许借当前行情或其他 venue 补图。

# Bound ledger display work without truncating account meaning

- **问题**：每次快照先将全部平仓记录和退出腿搬入 Python，再截取 100 条；关闭的前端抽屉仍生成列表，轮询完成还重复发出一次相同快照。
- **死胡同**：直接给原查询加 LIMIT 会让全期统计和最新资金成交一起被截断。重启前后的 HTTP 耗时也不能直接归因于改动，后台扫描预热会明显改变竞争负载。
- **有效路径**：完整统计交给 SQL 聚合，最近页面、最新资金成交分别查询，仅装载返回记录的退出腿；加入对应索引。关闭抽屉停止生成记录，但账户规则仍单独更新。2 万条合成历史的旧新快照逐字段一致，中位 204.3ms → 6.7ms；同进程交替测试 JSON 响应编码，保持相同内容。
- **通用规则**：分页仅限制展示，不限制统计和身份选择；性能比较固定输入与负载，并保留旧结果、失败与预热采样。
- **牵连**：`yoyo/monitor/monday_reclaim_paper.py`、`static/monday-reclaim-paper.js`、`static/reclaim-watchlist.js`，以及对应分页回归测试；不改变成交、费用或策略参数。

# Previous-week display must retain its own expiry and authority

- **问题**：纽约周一开始后，当前监控快照清掉上一周触价、区间读取正常返回 developing，前端却把它视为锚点读取失败；只绑定选中交易的 primitive 也无法保留同币历史仓位。
- **死胡同**：重试 developing 不能生成尚未收盘的周一区间。直接把旧区间改成本周，或放宽订单上下文的周界校验，会把历史参考变成新交易依据；固定减七天还会跨错夏令时。
- **有效路径**：严格 read 和执行快照保持原义。独立只读展示接口按纽约日历读取上一完整周，返回 current_week/display_week；旧线保留原 expires_ms，渲染时必须与新周一 start_ms 相等。历史仓位以币种和成交 ID 独立维护 primitive，退出时刻决定框终点，当前选中交易只决定定位。
- **通用规则**：先区分“正常形成中”和“读取失败”，再分别验证数据归属周、画线有效期、交易权限。保留历史不能通过伪造当前状态实现。
- **牵连**：monday_chart_levels.py、monday_display.py、monday-fib-chart.js、reclaim-chart.js。测试覆盖两次夏令时切换、当前严格读取不回退、展示不修改原快照、多仓位去重与换币清理。历史框仍受已加载成交记录和 K 线时间范围限制。

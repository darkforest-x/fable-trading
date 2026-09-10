# 特征 ready 不能替代当前市场覆盖

- **问题**：监控首页把持久 `counts.ready` 显示成“窗口已就绪”，但该计数可来自前一轮 feature state；新冷轮尚在追平收盘，且短历史合约也不足 warmup。
- **死胡同**：用 market row 的 `phase=ready` 或强制的 `stale=false` 判断最新收盘，会把已保存的结构状态误作当前行情可用性。
- **有效路径**：概览只以本轮 `scan.status/completed/total` 描述“已扫描”“预热/追平中”或“已覆盖，按收盘刷新”，不把 phase 计数写成实时全市场 ready。
- **通用规则**：任何运行看板都要区分 feature 状态、扫描覆盖和最新收盘三种事实；前两者都不能自动证明第三者。
- **牵连**：`yoyo/monitor/static/app.js`、`tests/monitor/frontend_cards.test.cjs`、`analysis/p0_spike_v1_monitor_migration_20260911.md`。

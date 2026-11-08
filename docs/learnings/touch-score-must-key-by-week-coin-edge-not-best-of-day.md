# 触价评分要按「周 × 币 × 方向」对应到那一次触价，不能取币种当日最高分

- **问题**：纪律 App 2.5.0 的「最新触价」列表在每行显示评分，取的是 `TouchFeed.scoreBySymbol`，也就是该币在最近信号里的最高分（owner 2026-11-08 追问后核对）。结果有两种错：同一个币上下沿都触价时显示的是另一边的分数；切到「本周」后，周一到周四的触价都显示「—」。
- **死胡同**：
  - 继续用 `/v1/monitor/paper` 的 signals：它只是监控的最近 100 条（实际覆盖约 3 天），而本周有 540 多次触价，补不全。
  - 把分数加到 `/v1/market/monday-fib`：这个接口不需要令牌，经公网隧道可以访问。模型评分不是公开行情，放进去等于对外泄露。
  - 按币名匹配加时间就近：上沿和下沿会混在一起。
- **有效路径**：监控里每个（周, 币, 方向）只有一条信号（实测没有重复），方向和触价边对应：做多（+1）↔ 下沿，做空（−1）↔ 上沿。新增需要令牌、只读的 `/v1/monitor/touch-scores`：最近一页每次请求都读，本周更早的历史分页缓存 5 分钟。只输出 symbol / side / 时间 / score / status，不带模型特征。App 按 `symbol|edge` 取分；服务未更新（接口 404）时，退回到「同币、同方向、观察时间相差 10 分钟以内」的最近信号。本机实测：本周 553 次触价对上 541 次，剩下 12 次是监控开始前就已触价的历史记录，本来没有评分。
- **通用规则**：给某个事件显示派生数值（评分、盈亏、标签）之前，先确认数据源里这类事件的唯一键，按这个键关联；不要按「同一个币」聚合后取极值。新字段要放进哪个接口，先看这个接口是否需要令牌。
- **牵连**：`yoyo/discipline_app/monitor_feed.py`（`project_touch_scores`、`MonitorFeed.touch_scores`）、`api.py`、`native/SpikeDiscipline/Monitor/TouchFeed.swift`（`score(_:_:)`、`match`）、`tests/discipline_app/test_monitor_feed.py`、`SpikeDisciplineTests/TouchScoreTests.swift`。

# 按当前账户裁剪快照时，要保留被跨账户读取的字段

- **问题**：1.618 模拟盘首页每 10 秒拉一次 `/api/monday-reclaim-paper`，单次 1.36 MB。其中 1.18 MB 是 13 个账户的明细，而页面一次只显示一个账户（spike 账户只占 12 KB）。
- **死胡同**：最初打算除当前账户外一律只返回汇总，但前端的 `heldSignals()` 会遍历所有账户的 `open`，标出今日触价列表里“跨日仍持有”的信号；`SpikeReclaimWatchlist.holdingTrack()` 也按全部账户的持仓选择账户。按账户全部裁掉会悄悄改变这两处结果。另外，快照里每 10 秒变化的字段只有持仓报价，用 ETag/revision 判断“未变化”拦不住重复下载。
- **有效路径**：逐个查前端对“其他账户”字段的用法：`summary()` 只用净值和统计，策略书只调 `getPnl`，`open` 有跨账户读取，`closed`/`record_closed` 只给当前账户用。所以只裁其他账户的 `closed`/`record_closed`，并标 `detail_omitted`；统计、净值、最近成交、持仓的算法和以前完全一样。不带 `track` 参数或参数未知时，返回原来的完整快照。前端检测到 `detail_track` 与当前账户不一致时立即重取；重取期间“已结束”页签显示“正在读取记录”，不显示 0 条。
- **通用规则**：给多实体快照做按需裁剪前，先查每个字段在前端的所有读取点，区分“只读当前实体”和“跨实体聚合”。只裁前者，并让旧请求路径保持原样作为回退。切换实体后的空窗期必须显示加载中，不能显示成“没有数据”。
- **牵连**：`yoyo/monitor/monday_reclaim_paper.py::snapshot(track=)`、`static/monday-reclaim-paper.js`（`detailPending`）、`static/startup-hints.js`（预取带上同一个默认账户）。测试：`tests/monitor/test_monday_reclaim_paper.py::test_track_snapshot_omits_only_other_accounts_closed_pages`、`tests/monitor/frontend_paper_detail_track.test.cjs`。

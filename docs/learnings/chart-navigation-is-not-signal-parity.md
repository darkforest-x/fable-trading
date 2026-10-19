# Opening a chart does not verify its indicator or signal parity

- **问题**：前端有 V13.1 信号，用户在 TV 看不到。检查时当前布局只有周一区间脚本，没有 SPIKE V13.1。
- **死胡同**：仅核对合约和周期就说“已同步”，或者凭当前布局缺指标就推断所有历史差异原因，都会超出证据。
- **有效路径**：卡片提供合约、周期、记录 ID、确认收盘与对应开盘时间，以及版本、模式、候选来源、显示开关、数据窗口核对步骤。保留 `chart_parity_verified=false`；未改动计算、通知与订单设置。
- **通用规则**：逐笔一致性需核对数据身份、参数、时间语义和显示状态；导航成功只证明导航。
- **牵连**：`yoyo/monitor/static/app.js`、`tests/monitor/frontend_cards.test.cjs`、Notion 第 48 条。当前布局观察不能替代用户原始缺失案例的复现。

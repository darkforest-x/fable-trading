# Pine 官方编译 smoke 不能代替精确回测 parity

- **问题**：冻结 Pine 在 TradingView 的正确 symbol、15m 和 Pine v6 下以 0 error 加载，容易被误读成“官方回测已经验证”。但编译页面可能只加载研究窗口之外的当前 K 线，而且设置输入、逐笔成交与费用语义都没有证据。
- **死胡同**：用一个 `official_compiler_passed` 布尔值代表全部 TradingView 验证。这样源码能编译就可能打开 forward 门，即使 Start/End 仍是默认值、Strategy Report 没有交易、97/110 笔账本没有导出。
- **有效路径**：把门拆成三层：第一层只验官方编译、源码 hash、venue、timeframe 与 Pine 版本；第二层必须从设置证据验精确研究窗口和冻结输入；第三层必须用完整导出逐笔验 entry/exit time、price、commission 与 net。缺任一层就保留 parity=false。
- **通用规则**：看到“编译成功”时，第一步先问它证明的是语法/加载、输入合同还是成交账本；每种证据使用独立字段与独立 receipt，禁止用推断填补未观察字段。
- **牵连**：`scripts/design_pine_eth_15m_paper_protocol_v2.py`、`scripts/reconcile_pine_eth_15m_tradingview.py`、`tradingview_compile_receipt_v12f.json`、V9 110 笔与 V12F 97 笔精确对账门、TradingView 可见历史范围。

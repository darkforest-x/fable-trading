# 浏览器编译 Smoke 必须先落在安全市场窗口

- **问题**：为验证 Pine 官方编译器，直接打开 TradingView 默认图表会先加载当前市场窗口；即使脚本有 `dateAllowed` 门且产生 0 笔交易，浏览器仍可能显示 repository holdout 之后的价格。
- **死胡同**：把“只编译、不回测”理解成不会接触评估数据。编译动作本身不读取本地 holdout，但外部图表的默认时间位置是独立状态，不能由脚本的交易日期门替代。
- **有效路径**：编译前先在空白/安全标的或明确的 pre-holdout 日期打开图表，再粘贴源码；编译回执分别记录 `compiler_run`、可见图表区间、策略实际交易数和 holdout 是否被用于计算。若默认窗口已暴露，立即停止收益读取，只保留编译事实并登记事故。
- **通用规则**：任何浏览器端模型/策略 smoke 都要把“页面默认数据上下文”列为第一道门；代码级 fail-closed 不等于 UI 级数据隔离。
- **牵连**：TradingView Pine Editor、Deep Backtesting 权限、`researchEnd` 日期门、holdout 纪律、`tradingview_compile_receipt.json` 与后续逐笔 parity 流程。

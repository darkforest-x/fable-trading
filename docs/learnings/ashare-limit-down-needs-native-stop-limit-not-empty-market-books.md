# 跌停无买盘不能仅靠给市价单返回空盘口表达

- **问题**：将周一 1.618 挂单迁移到 A 股时，T+1 和跌停必须约束真实框架成交，不能在结果里事后删掉不合法卖出。
- **死胡同**：原有永续适配器立即挂 STOP_MARKET。安装版 NautilusTrader 1.231.0 的 `determine_market_fills_with_simulation` 收到空模拟盘口后会回退标准市价撮合，因此空盘口本身挡不住跌停卖单；同时成交当天挂保护单会违反 T+1。
- **有效路径**：A 股 Equity 适配器等下一交易日开盘才挂退出单；使用原生 STOP_LIMIT，卖出限价为当日跌停价，触发后无流动性则继续等待。`determine_limit_fills_with_simulation` 保留空结果，不作市价回退。开盘跳空、单边锁板、后续恢复流动性、当日不可卖与真实费用分别用原生框架合成行情测试。
- **通用规则**：迁移市场前，先检验撮合引擎在“不可成交”条件下的具体回退行为；盘口模型名和返回值不足以证明执行约束已经生效。
- **牵连**：`yoyo/layers/l3_backtest/nautilus_ashare.py`、`tests/evaluation/test_nautilus_ashare.py`；不改变原有币圈适配器或执行策略。小时 OHLC 无法证明盘口排队和容量，只能给出明确的研究假设。

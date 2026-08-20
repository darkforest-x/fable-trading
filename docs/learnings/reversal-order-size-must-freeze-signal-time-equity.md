# 反转订单数量必须冻结信号时点权益

- **问题**：Pine 在确认 bar 收盘提交反转单时，`qty` 已由当时的 `strategy.equity` 算定；Python 虽冻结了信号价和止损距离，却在下一根开盘平掉旧仓后才用已实现权益算新仓，导致反转后的资金曲线与 Pine 发生细小漂移。
- **死胡同**：只检查“使用 signal close 作为 sizing price”并据此宣称数量已冻结。风险公式里的价格会约掉，真正仍在漂移的是权益分子；普通空仓入场看不出，只有隔夜跳空式反转才暴露。
- **有效路径**：把数量契约拆成价格基准与权益基准两轴；信号 bar 先按当前持仓的收盘浮盈亏和已付入场佣金计算 marked equity，把该值随 pending order 一起冻结，下一根开盘只负责成交，不再重算数量。
- **通用规则**：审计 next-open 回测时，不只核对信号与成交价格，还要逐项核对订单提交时已经冻结的 `qty`、stop ticks、权益、手续费和反转净额；状态值的计算时间与价格时间同等重要。
- **牵连**：`yoyo/layers/l3_backtest/pine_allin_v7.py`、V9 `strategy.equity` 风险仓位、反转订单、逐 bar 权益/回撤、离线 Docker replay 与 TradingView trade-export parity。

# 信号接口能接通，不代表参考持仓已经变成可执行策略

- **问题**：Notion #59 询问能否把信号中心接到 OKX 策略页。2026-10-16 实际接口确认中心为 SPIKE V13.1，`orders_enabled=false`、`is_trade=false`，价格与退出是已收盘 OHLC 上的参考投影。
- **死胡同**：若只把多空提醒改成 OKX JSON，就会忽略退出、动态保护与成交状态。当前 Pine 是 indicator，回踩模式只暴露确认／多头／空头提醒，原版退出提醒在回踩模式被关闭；不能套用 strategy 订单成交提醒。把参考止损触及后的收盘投影转发，也无法在原触及价成交。本轮通过源码检查排除这些捷径，没有发单试错。
- **有效路径**：先核对运行中的事件身份，再按入场、保护、退出、成交、持仓归属逐项映射。OKX 官方安装指南 §3.4 还要求原生程序固定 IP 接入经客服处理；Signal Bot 通用消息没有逐笔绝对止损更新字段，固定机器人止损不能等同于 2R 收盘启用、随后各收盘更新的 ATR 保护。目标是 OKX“我的策略”时评估 Signal Bot；目标是保留本地规则时优先评估独立 API 执行模块，并先做模拟盘对账。
- **通用规则**：把接口可达、事件语义、成交事实和授权分别验收。重复／迟到／重启不能补开旧单；同币跨周期要明确归属；反向原始信号只退出，不自动开反向仓。通知的 30 分钟新鲜度不能直接当作可按次开价交易的容忍度；传输 maxLag 也不能替代信号原始时间检查。动态保护应在交易所托管，逐次确认有效；实际成交价、成本和触发价类型另行对账。
- **牵连**：`yoyo/monitor/v130_signals.py`、`v130_policy.py`、`server.py`、`yoyo/evaluation/pine/spike_burst_v13_1.pine`；旧 `yoyo/layers/l4_execution/` 是 TP5/SL2 与 active bundle 路线，`yoyo/copier/okx/` 是跟单仓位规则，均不能直接冒充 V13.1 执行器。`tests/monitor/test_v130_signals.py` 本次 17 项通过，仅核验原参考状态机，不证明 OKX 端到端可用。任务：[Notion #59](https://app.notion.com/p/3fb8856479af8092b673f3b74ed5d6f4)。官方依据：[接入指南](https://www.okx.com/help/how-to-set-up-signal-trading-bot-with-tradingview)、[消息规格](https://www.okx.com/help/signal-bot-alert-message-specifications)、[模拟盘与持仓模式 FAQ](https://www.okx.com/help/trading-signal-bot-faqs)。以上为方案核查，未改变策略、成本、新鲜度、账户或实盘授权。

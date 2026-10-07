# Arthur 行情分析频道历史开单分析

频道：`https://discord.com/channels/1004707886657699901/1131521990814089276`

抓取范围：`2026-01-02T21:57:28.668000+00:00` -> `2026-06-15T16:59:07.606000+00:00`  
原始抓取：231 条  
有效文本消息：226 条

## 结论

- Arthur 不是标准信号流，更多是交易计划、盘中观察、跟单账户更新。
- 真正可安全自动执行的结构化开仓只有 2 条。
- 需要 AI/人工复核的开仓类消息 3 条，主要原因是缺少入场/止损/止盈，或方向与 TP/SL 互相矛盾。
- 计划/观察类消息 116 条，只应转发或记录，不应直接下单。
- 当前样本太少，不能像 ChartPrime/Eliz 那样给出可靠胜率和收益曲线。

## 消息分类

- 结构化开仓：3 条
- 手动开仓但缺关键价位：2 条
- 成交/平仓/移动止损更新：22 条
- 计划/观察/如果触发：116 条
- 其他/噪音：83 条

## 可归并交易

- 总开仓/执行样本：5 条
- 可自动跟单：2 条
- 无效结构化：1 条
- 缺价位手动开仓：2 条
- 已成交更新：4 次
- 部分平仓：2 次
- 移动止损：2 次
- 多/空：5 / 0

## 明细

- 2026-06-09T15:00 `BTC` LONG | structured | entry 60780.0 | SL 58500.0 | TP 66400.0 | partial_profit_be
- 2026-06-12T14:06 `BTC` LONG | structured | entry 63680.0 | SL 56900.0 | TP 97000.0 | unknown
- 2026-06-12T16:34 `ETH` LONG | manual_missing_levels | entry - | SL - | TP - | closed
- 2026-06-12T18:28 `CRV` LONG | manual_missing_levels | entry - | SL - | TP - | closed
- 2026-06-13T18:38 `CRV` LONG | structured | entry 0.2276 | SL 0.28 | TP 0.2072 | invalid_levels

## 月度

- 2026-06: 5 条开仓/执行样本 | 部分盈利/保本 1 | 关闭 2 | 无结果 1 | 无效 1

## 自动执行建议

- 可执行：`BTC Long Risk: 2% Entry: 60780 TP: 66400 SL: 58500 Position Filled: No`
- 可执行：`BTC Positional Long Risk: 3% Entry: 63680 TP: 97k SL: 56900 Position Filled: Yes`
- 拒绝/需复核：`CRV Long Risk: 2% Entry: 0.2276 TP: 0.2072 SL: 0.28`，因为 Long 的 TP 低于入场且 SL 高于入场。
- 只记录不下单：`Looking for...`、`Watching...`、`will long if...`、`gameplan...`。
- 缺价位时只转发/提醒：`Longed ETH 1% Risk`、`Longed CRV 1% 0.2220 SL`。

## 文件

- 原始消息：`data/arthur_raw_messages_2026.json`
- 归并交易：`data/arthur_trades_2026.json`

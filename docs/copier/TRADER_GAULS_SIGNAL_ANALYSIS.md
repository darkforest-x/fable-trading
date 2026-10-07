# Trader Gauls 频道近半年交易统计

频道：`https://discord.com/channels/1004707886657699901/1280373415680806984`

抓取范围：`2026-01-01T17:00:00+08:00` -> `2026-06-11T00:59:00+08:00`  
原始消息：291 条  
可归并开单：69 单

## 结论

- 可确认盈利/至少部分止盈/已风控到保本：34 单
- 明确止损亏损：3 单
- 未完成或无法确认结果：32 单
- 已管理样本非亏损率：91.9%
- 严格口径：完整 TP/明确盈利 vs SL = 80.0%
- 多单/空单：55 / 14
- Entry 类型：{'numeric': 52, 'cmp': 17}

## 结果分布

- 完整/明确盈利：12
- 部分止盈：10
- 保本或 risk-free 管理：12
- 止损亏损：3
- 未知：32

## R 倍数口径

有明确 R/收益倍数的更新：30 单。  
可见净 R 粗略合计：55.00R（止损按 -1R 估算）。  
正向更新平均：1.93R。

注意：这个频道很多 Entry 是 `CMP`，且消息常写 `book some profit / make it risk free`，不是固定分批 TP；所以这里不强行按 500U 全仓 5x 复利算金额，避免把 CMP 和小数价格误差放大成假收益。

## 月度

- 2026-01: 9 单 | 盈利/风控成功 3 | SL 0 | 未知 6
- 2026-02: 5 单 | 盈利/风控成功 1 | SL 1 | 未知 3
- 2026-03: 17 单 | 盈利/风控成功 8 | SL 2 | 未知 7
- 2026-04: 13 单 | 盈利/风控成功 8 | SL 0 | 未知 5
- 2026-05: 16 单 | 盈利/风控成功 10 | SL 0 | 未知 6
- 2026-06: 9 单 | 盈利/风控成功 4 | SL 0 | 未知 5

## 常见话术

- 开仓：`$XXX Buying Setup: Entry ... TP ... SL ...`
- 做空：`$XXX Short Setup: Entry ... TP ... SL ...`
- 部分止盈/风控：`book some profit`、`make it risk free`、`Move the Stop at Breakeven`
- 明确盈利：`Target achieved`、`done and dusted`、`+xR`
- 止损：`stopped out`、`got stopped`

## 最近开单样本

- 2026-05-26T15:39 RENDER long -> unknown | R=1.0 | $RENDER BUYING SETUP:
- 2026-05-27T13:43 VIRTUAL long -> breakeven_or_riskfree | R=1.0 | $VIRTUAL Buying Setup:
- 2026-05-30T00:08 NEAR long -> unknown | R=- | $NEAR Buying Setup:
- 2026-06-01T23:59 INJ long -> breakeven_or_riskfree | R=1.0 | $INJ Buying Setup:
- 2026-06-02T13:19 FET long -> unknown | R=- | $FET swing Buying Setup:
- 2026-06-03T22:58 JUP long -> breakeven_or_riskfree | R=- | $JUP Buying Setup:
- 2026-06-04T15:55 BTC short -> unknown | R=- | $BTC Short Setup:
- 2026-06-05T14:14 PENDLE short -> unknown | R=- | $PENDLE Short Setup:
- 2026-06-06T02:19 BTC short -> win_tp | R=1.0 | $BTC Short Setup:
- 2026-06-06T18:12 ETH short -> unknown | R=- | $ETH SHORT SETUP:
- 2026-06-08T14:34 HYPE long -> partial_tp | R=1.0 | $HYPE Buying Setup:
- 2026-06-09T14:27 WLD long -> unknown | R=- | $WLD Buying Setup:

## 文件

- 原始消息：`data/trader_gauls_raw_messages_2026.json`
- 归并交易：`data/trader_gauls_trades_2026.json`

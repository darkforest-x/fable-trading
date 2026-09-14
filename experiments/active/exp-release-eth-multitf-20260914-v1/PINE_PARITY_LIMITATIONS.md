# Pine 与 Python 回放的已知对账边界

- 本文件是 E5/C0–C5 的 TradingView 研究端口，未在 TradingView 原生编译，也未用任何行情数据运行；不能据此宣称 Pine 已编译、交易数、收益或逐笔一致。
- Python 汇总回放在反向信号的下一根开盘把旧仓平掉并建立新仓；Pine 取消旧止损，以单个 `strategy.entry` 自动反转，并给新 ID 提交 `strategy.exit`。不再同时提交冗余 `strategy.close`。TradingView broker emulator 的订单排序、成交价及跳空时止损优先级尚未做原生逐笔验收。
- `margin_long/short=0` 仅对齐 Python 未建模强平的研究口径；仓位数量仍固定为信号收盘权益 / 收盘价，约 1x。下一开盘价格、手续费与反转平仓盈亏会使成交后实际杠杆略偏离 1x。没有交易所维持保证金、合约张数/最小委托量、tick 舍入模型。
- 默认 C0 为原信号的执行修正版；Auto 根据三个周期复现开发期冻结候选，其中1h/4h已验证失败。显式 C0–C5 供复核。日期过滤不意味着平台不会加载过滤范围外的 K 线，因此本轮没有把脚本添加到含 holdout 的现有图表。
- Python 的父 OHLC 路径采用确定性高低点顺序；TradingView 的 broker emulator 使用其自身路径规则。即使信号和参数相同，触及止损、跳空与 BE 的成交时点仍可能不同。
- 两端都按已确认父周期收盘产生信号、下一根开盘处理订单，且 Pine 不使用低周期请求或未来数据；这只是设计意图，不能代替原生编译与 ledger parity 证据。

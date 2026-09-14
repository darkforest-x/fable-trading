# ETHUSDT.P 3 分钟 SPIKE V8：2026-09-01 至 09-14 逐笔核对

这是所有固定规则交易与全部 V8 信号的**对账审计**，不是参数优化、收益宣称或实盘指令。默认交互视图为原始 V8 串行回放；可切换净目标 `next_bar`、`ohlc`、`olhc` 情景。`ohlc`/`olhc` 仅是明确假设的 bar 内路径，不是观察到的真实路径，也不是收益上下界。

- 时段：北京时间 2026-09-01 00:00 至 2026-09-14 21:19；最后完整 3 分钟 bar 在北京时间 21:18 收盘（UTC `2026-09-14T13:18:00+00:00`）。
- 数据：OKX `ETH-USDT-SWAP` 已完成 3 分钟 candle；来源端点 `https://www.okx.com/api/v5/market/history-candles`，来源收据和尾部哈希保留在实验结果目录。
- 本次按 owner 指定近期范围专项核对：这是列出各冻结配置第 1 次专项消耗 holdout；不是新的盲验收，未搜索或重新选择参数。
- R 是初始开仓价到初始止损价的距离。若每笔初始价格风险固定 1U，毛R/费用R/净R分别对应毛U/费用U/净U；0.2% 是冻结的名义本金往返成本假设，不能把价格保本自动记成净保本。
- 原始 V8 有 `20` 条串行记录（`19` 条自然平仓 + `1` 条截止估值）；`next_bar` / `ohlc` / `olhc` 分别有 `22` / `22` / `22` 条记录（自然平仓分别 `22` / `22` / `22`，截止估值分别 `0` / `0` / `0`）。原始 arm 的 `carry_in` 若存在，表示入场早于 9 月但仓位延续到审计窗；净目标 arms 从 9 月起始空仓，且不应用账户容量过滤。

## 怎样检查

下方 HTML 的交互面板默认显示原始 V8 全部交易。点击交易行或用前后箭头，可查看信号前 30 根至退出后最多 15 根的本地 OHLC 图。图中叠加入场、初始止损，以及适用时的成本保本与净目标价格。悬停 K 线可见北京时间和 OHLC。`截止仍持仓，仅收盘估值` 是 censor，不应与自然平仓混为一谈。

MFE 给出的是保守范围：止损退出 bar 的有利极值可能发生在止损之后，不能称为退出前已实现最高点。图表同样不把 `ohlc`/`olhc` 路径称作真实交易路径。

下载原始 CSV：

- [全部 V8 信号](../../experiments/active/exp-spike-eth3m-september-trade-audit-20260914-v1/results/all_v8_signals.csv)
- [原始 V8 交易（中文列）](../../experiments/active/exp-spike-eth3m-september-trade-audit-20260914-v1/results/original_trades_zh.csv)
- [原始 V8 交易](../../experiments/active/exp-spike-eth3m-september-trade-audit-20260914-v1/results/original_trades.csv)
- [原始 V8 实际止损路径](../../experiments/active/exp-spike-eth3m-september-trade-audit-20260914-v1/results/original_stop_path.csv)
- [next_bar 交易](../../experiments/active/exp-spike-eth3m-september-trade-audit-20260914-v1/results/next_bar_trades.csv)
- [ohlc 交易](../../experiments/active/exp-spike-eth3m-september-trade-audit-20260914-v1/results/ohlc_trades.csv)
- [olhc 交易](../../experiments/active/exp-spike-eth3m-september-trade-audit-20260914-v1/results/olhc_trades.csv)
- [检查用 bars](../../experiments/active/exp-spike-eth3m-september-trade-audit-20260914-v1/results/inspection_bars.csv)

## 可复现命令

```bash
# Owner-approved source acquisition and fixed-rule builder (只在收据授权后运行)
PYTHONPATH=. .venv/bin/python -m yoyo.evaluation.spike_september_trade_audit fetch
PYTHONPATH=. .venv/bin/python -m yoyo.evaluation.spike_september_trade_audit run

# 本报告：仅消费 results/audit_payload.json
PYTHONPATH=. .venv/bin/python scripts/report_spike_september_trade_audit.py
```

## 审计口径与零假设

原始 arm 保持冻结 V8 准入、5-bar / 0.2 ATR / 2 ATR floor 初始止损、2R 收盘 arm、4 ATR trail 与 20bp 往返成本。独立原始回放的 parity 是零变化对照：它只检验同一入场/退出实现的一致性，不是随机基准，也不构成新策略证据。

AUC、随机对照收益、置换检验和方向性收益宣称不适用于这份逐笔对账；本交付的等价严格检查是：每笔可追溯到信号、入场、退出、成本和 cutoff，且原始/情景 arms 的范围与是否串行明确披露。

原始20笔与独立入场回放全部匹配，有效止损逐bar重算全部匹配；86条交易记录的价格/R重算最大误差6.11e-14，112项相关测试通过。交互脚本已做86图/全部时序切换的最小DOM逻辑检查；未做浏览器像素级验收。此前全库无关注册/迁移哈希失败未在本轮修复或重跑，详见validation.json。

## 风险与诚实声明

本审计不改变冻结规则，不产生交易指令。费用之外未模拟资金费、滑点、撮合队列或断线风险。OKX candle 的 OHLC 不能辨识真实 intrabar 次序；仅 `ohlc` / `olhc` 情景把该不确定性显式暴露。

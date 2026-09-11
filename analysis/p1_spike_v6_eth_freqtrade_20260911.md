# SPIKE V6 ETH Freqtrade bridge：冻结镜像执行检查

生成时间：2026-09-11。此文记录一个离线、来源镜像研究，不是 TradingView 原生回测、实盘建议或收益结论。

## 口径与复现

- V6 规则：冻结 Pine commit `6c12660`，Pine SHA `e15854bc577e5548c5799dcbba87d8b218c195fedf562940b621b9d7ef57c882`，oracle SHA `e84690c02e7b03291cc3037eab37ac495cc2ece79326e24411cfa72aa1214d4b`。
- 数据：已存在 OKX ETH-USDT-SWAP 冻结 30m 源，SHA `80dc85ee0b926239b20a3e733e425550b2a863cf66f27f8c1a396d31d252855a`；评估窗 2024-09-10 至 2026-09-10 UTC。
- 执行：next observed open 入场、单一活跃仓、双边各 10bp 费用、funding=0 占位。止损只读取前一根已收盘时的计划，不能用同根 high/low 更新后反查同根 low/high。
- 复现：`cd /Users/zhangzc/fable-trading/experiments/active/exp-spike-v1-eth-stops-20260911/freqtrade_v6 && /Applications/Xcode.app/Contents/Developer/Library/Frameworks/Python3.framework/Versions/3.9/Resources/Python.app/Contents/MacOS/Python build_v6_bridge.py && /Applications/Xcode.app/Contents/Developer/Library/Frameworks/Python3.framework/Versions/3.9/Resources/Python.app/Contents/MacOS/Python write_frozen_ohlcv.py && /Applications/Xcode.app/Contents/Developer/Library/Frameworks/Python3.framework/Versions/3.9/Resources/Python.app/Contents/MacOS/Python run_freqtrade_v6.py && /Applications/Xcode.app/Contents/Developer/Library/Frameworks/Python3.framework/Versions/3.9/Resources/Python.app/Contents/MacOS/Python render_cases.py`。`run_freqtrade_v6.py` 才是实际调用隔离 Freqtrade 2026.8 的完整 runner；本次图形修订只读取已完成 ledger 与冻结 OHLC，没有重跑该命令或重取行情。
- 钱包与结果单位：dry-run wallet 10,000 USDT，fixed stake 1,000 USDT，isolated futures、1x leverage、最多一仓；累计收益率和相对回撤是该模拟钱包的 Freqtrade 输出，不是 V1 的独立净R，也不是复利账户或实盘收益。funding=0 占位。

## 因果与框架检查

`test_v6_bridge.py` 通过。receipt 对 30m/1H/4H 的 501、2501、中段与末段前缀检查确认：追加未来 bars 不改变此前候选或当时 active stop。Freqtrade 实际成交逐笔均有信号/止损计划输入；入场缺失和止损输入缺失均为 0。单活跃仓使 467 个 30m 双向候选中只有 215 笔实际成交，这个差异是框架仓位约束的结果，不能当作 V6 漏信号。

## 全窗基线（双向）

| 周期 | 候选 | Freqtrade 成交 | PF | 胜率 | 累计收益率 | 最大相对回撤 |
|---|---:|---:|---:|---:|---:|---:|
| 30m | 467 | 215 | 1.011 | 31.2% | 0.489% | 7.22% |
| 1H | 246 | 119 | 0.998 | 25.2% | -0.070% | 5.20% |
| 4H | 60 | 32 | 2.289 | 40.6% | 13.529% | 2.27% |

4H 只有 32 笔，30m/1H 的结果也没有匹配随机入场对照；这些数不能支持 edge 或盈利声明。long-only 对照也已执行，保存在同一 receipt 中，未用来选择 V6 规则。

| 周期 | 双向：候选/成交/拒绝 | long-only：候选/成交/拒绝 | 双向 PF | long-only PF |
|---|---|---|---:|---:|
| 30m | 467 / 215 / 252 | 235 / 127 / 108 | 1.011 | 1.032 |
| 1H | 246 / 119 / 127 | 119 / 73 / 46 | 0.998 | 0.882 |
| 4H | 60 / 32 / 28 | 31 / 20 / 11 | 2.289 | 2.073 |

拒绝数是 `max_open_trades=1` 的重叠候选或边界，非策略的明确拒绝字段（该字段均为 0）。

## 30m 单变量开发/后段检查

这是固定的单变量网格，不是 Hyperopt。30m 双向开发 baseline (2/4 ATR) 为 118 笔、PF 0.957、-1.110%；initial 1.5 为 118 笔、PF 0.970、-0.774%；trail 5 为 116 笔、PF 0.969、-0.793%。补跑同一后段 baseline：98 笔、PF 1.140、+2.350%；initial 1.5 为 PF 1.152、+2.526%，trail 5 为 PF 1.161、+2.547%。差异很小，不能构成参数优化结论。

## Historical trade reviews

The two figures use the same frozen global OHLCV and actual Freqtrade ledger. Bodies and wicks are frozen OHLC, not a close-only line. The diamond is the V6 signal close; the triangle is the actual next-open fill, and the red X is the actual ledger exit. In both selected cases signal close and next-open share the same boundary by the stated execution clock, so their adjacent labels deliberately show the same timestamp instead of inventing a lag.

The orange initial SL is drawn only from entry to actual exit. The purple step is each `active_stop` known before that candle; it stops at the actual exit and is never extended backward from its final value. Blue shading begins after the exit and covers 72 bars of historical review context only. `row-N:open_timestamp:side` in each inset is the stable row identity in the named exported Freqtrade ledger; the export has no separate Freqtrade trade-id column.

![4H long winner — frozen OHLC, causal stop and actual ledger points](../../experiments/active/exp-spike-v1-eth-stops-20260911/freqtrade_v6/results/v6_4h_winner.png)

![1H short loss — frozen OHLC, causal stop and actual ledger points](../../experiments/active/exp-spike-v1-eth-stops-20260911/freqtrade_v6/results/v6_1h_loss.png)

## 风险与诚实声明

这里的 V6 是 Python 镜像逐式构建、fixture/前缀因果检查加 Freqtrade 执行对照；未进行 TradingView 原生 ETH 两年逐 bar 编译验证。每笔实际成交都有唯一 `(open_date,is_short)`，并且 receipt 的信号/stop 输入缺失均为 0；stop map 的 key 含 entry time，因此一个被单仓忽略的候选不能为当前交易提供 stop。没有 funding、滑点、流动性或多仓账户模型，也没有随机对照或显著性检验。数据包含 >=2026-05-04，属于 owner 已授权的历史研究，已暴露，历史消费次数未知，不能称未见样本外。结果仅用于核查 V6 形态镜像、next-open/止损时钟与框架成交边界；不得改动 live V1、Pine、通知或风险参数。

产物：`experiments/active/exp-spike-v1-eth-stops-20260911/freqtrade_v6/results/freqtrade_execution_receipt.json`、`freqtrade_run_summary.csv` 与各逐笔 gzip ledger。

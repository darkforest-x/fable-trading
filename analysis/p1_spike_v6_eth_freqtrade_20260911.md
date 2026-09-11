# SPIKE V6 ETH Freqtrade bridge：冻结镜像执行检查

生成时间：2026-09-11。此文记录一个离线、来源镜像研究，不是 TradingView 原生回测、实盘建议或收益结论。

## 口径与复现

- V6 规则：冻结 Pine commit `6c12660`，Pine SHA `e15854bc577e5548c5799dcbba87d8b218c195fedf562940b621b9d7ef57c882`，oracle SHA `e84690c02e7b03291cc3037eab37ac495cc2ece79326e24411cfa72aa1214d4b`。
- 数据：已存在 OKX ETH-USDT-SWAP 冻结 30m 源，SHA `80dc85ee0b926239b20a3e733e425550b2a863cf66f27f8c1a396d31d252855a`；评估窗 2024-09-10 至 2026-09-10 UTC。
- 执行：next observed open 入场、单一活跃仓、双边各 10bp 费用、funding=0 占位。止损只读取前一根已收盘时的计划，不能用同根 high/low 更新后反查同根 low/high。
- 命令：`python3 build_v6_bridge.py && python3 write_frozen_ohlcv.py && python3 run_freqtrade_v6.py`（本次只读取已完成产物，没有重取行情）。

## 因果与框架检查

`test_v6_bridge.py` 通过。receipt 对 30m/1H/4H 的 501、2501、中段与末段前缀检查确认：追加未来 bars 不改变此前候选或当时 active stop。Freqtrade 实际成交逐笔均有信号/止损计划输入；入场缺失和止损输入缺失均为 0。单活跃仓使 467 个 30m 双向候选中只有 215 笔实际成交，这个差异是框架仓位约束的结果，不能当作 V6 漏信号。

## 全窗基线（双向）

| 周期 | 候选 | Freqtrade 成交 | PF | 胜率 | 累计收益率 | 最大相对回撤 |
|---|---:|---:|---:|---:|---:|---:|
| 30m | 467 | 215 | 1.011 | 31.2% | 0.489% | 7.22% |
| 1H | 246 | 119 | 0.998 | 25.2% | -0.070% | 5.20% |
| 4H | 60 | 32 | 2.289 | 40.6% | 13.529% | 2.27% |

4H 只有 32 笔，30m/1H 的结果也没有匹配随机入场对照；这些数不能支持 edge 或盈利声明。long-only 对照也已执行，保存在同一 receipt 中，未用来选择 V6 规则。

## 30m 单变量开发/后段检查

开发段仅比较初始止损轴 (1.5/2.0/2.5 ATR) 与跟踪轴 (3/4/5 ATR)，没有混合搜索。开发段的最高 PF 仍低于 1（initial 1.5: 0.970；trail 5.0: 0.969）。预先带入后段的两个候选分别为 PF 1.152 与 1.161，样本为 98 与 93 笔；后段同向但差异很小，不能构成参数优化结论。

## 风险与诚实声明

这里的 V6 是 Python 镜像逐式构建、fixture/前缀因果检查加 Freqtrade 执行对照；未进行 TradingView 原生 ETH 两年逐 bar 编译验证。没有 funding、滑点、流动性或多仓账户模型，且没有随机对照、显著性检验或授权 holdout 评分。结果仅用于核查 V6 形态镜像、next-open/止损时钟与框架成交边界；不得改动 live V1、Pine、通知或风险参数。

产物：`experiments/active/exp-spike-v1-eth-stops-20260911/freqtrade_v6/results/freqtrade_execution_receipt.json`、`freqtrade_run_summary.csv` 与各逐笔 gzip ledger。

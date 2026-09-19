# SPIKE V11.2 六均线支撑单变量完整回放

**结论：两个周期均未通过本轮研究门，这项过滤不能据此升级为默认交易条件。**

日期：2026-09-19。638 个币完整重放，原基线逐笔复现。

- **15m**：全期每笔净 R -0.0950 → -0.0909，平仓笔数 7082 → 6945；后段每笔净 R -0.2921 → -0.2921，平仓笔数 3313 → 3245。
- **1h**：全期每笔净 R -0.0921 → -0.1090，平仓笔数 2199 → 2157；后段每笔净 R -0.2278 → -0.2359，平仓笔数 1077 → 1057。

## 结果

| timeframe | arm | period | closed | win_rate | mean_gross_r | mean_net_r | pf | random_mean_r | excess_vs_random_r | p_vs_random |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 15m | box_any | full | 7082 | 0.2938 | 0.0253 | -0.0950 | 0.8705 | -0.1834 | 0.0894 | 0.0855 |
| 15m | box_any | earlier | 3769 | 0.3386 | 0.1976 | 0.0782 | 1.1143 | -0.1690 | 0.2476 | 0.0040 |
| 15m | box_any | later | 3313 | 0.2430 | -0.1707 | -0.2921 | 0.6298 | -0.1998 | -0.0911 | 0.9305 |
| 15m | box_support | full | 6945 | 0.2947 | 0.0286 | -0.0909 | 0.8759 | -0.1815 | 0.0917 | 0.0840 |
| 15m | box_support | earlier | 3700 | 0.3403 | 0.2042 | 0.0856 | 1.1253 | -0.1650 | 0.2509 | 0.0050 |
| 15m | box_support | later | 3245 | 0.2428 | -0.1716 | -0.2921 | 0.6299 | -0.2004 | -0.0904 | 0.9360 |
| 1h | box_any | full | 2199 | 0.2783 | -0.0298 | -0.0921 | 0.8705 | -0.1091 | 0.0126 | 0.3933 |
| 1h | box_any | earlier | 1122 | 0.3253 | 0.0993 | 0.0381 | 1.0576 | -0.0170 | 0.0551 | 0.2409 |
| 1h | box_any | later | 1077 | 0.2293 | -0.1644 | -0.2278 | 0.7018 | -0.2062 | -0.0323 | 0.6592 |
| 1h | box_support | full | 2157 | 0.2754 | -0.0470 | -0.1090 | 0.8476 | -0.1089 | 0.0026 | 0.4693 |
| 1h | box_support | earlier | 1100 | 0.3200 | 0.0737 | 0.0129 | 1.0194 | -0.0215 | 0.0344 | 0.3383 |
| 1h | box_support | later | 1057 | 0.2289 | -0.1727 | -0.2359 | 0.6918 | -0.2008 | -0.0309 | 0.6417 |

R 是每笔初始止损风险单位；胜率为小数。净收益已扣原设定 0.2% 往返成本。所有均值只用已平仓交易。

## 条件与数据

A=原框内首次任一突破，B=A 且当根收盘严格高于 SMA/EMA 20、60、120 的最高值。先消耗原框首个突破，再过滤，拒绝后不能同框重试。其他动量、方向门没有恢复。两臂独立串行回放，允许过滤后释放仓位。

638 个 Binance 永续历史档案，5m 聚合，15m←1h 与 1h←4h；收盘信号区间 2024-09-10 至 2026-05-01（右端不含），2025-09-10 切前后段。原始数据全部允许研究，本次保持旧窗口只为复现。候选和截尾如下。

| timeframe | arm | original_candidates | support_rejections | eligible_candidates | closed | skipped_in_position | censored_boundary | risk_invalid |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 15m | box_any | 7095 | 0 | 7095 | 7082 | 8 | 4 | 1.0000 |
| 15m | box_support | 7095 | 139 | 6956 | 6945 | 7 | 4 | 0.0000 |
| 1h | box_any | 2206 | 0 | 2206 | 2199 | 5 | 2 | 0.0000 |
| 1h | box_support | 2206 | 43 | 2163 | 2157 | 4 | 2 | 0.0000 |

规则回测没有分类正类标签、训练集或 val AUC，也没有连续打分及 top-decile 组合，因此这些指标不适用；后段样本数见 later 行。零假设对照是同币×同月×同时间段×同 ATR/价格波动桶随机入场、同退出同成本。共同事件共享随机种子；随机对照按交易配对，不构成独立串行资金账户。

## 改善幅度与可用门

| timeframe | compare | period | months | valid_reps | a_closed | c_closed | a_mean_r | c_mean_r | diff_mean_r | ci95_low | ci95_high | a_total_r | c_total_r |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 15m | box_support − box_any | full | 20 | 2000 | 7082 | 6945 | -0.0950 | -0.0909 | 0.0041 | -0.0022 | 0.0099 | -672.8149 | -631.1775 |
| 15m | box_support − box_any | earlier | 13 | 2000 | 3769 | 3700 | 0.0782 | 0.0856 | 0.0073 | -0.0025 | 0.0140 | 294.9065 | 316.6032 |
| 15m | box_support − box_any | later | 8 | 2000 | 3313 | 3245 | -0.2921 | -0.2921 | 0.0000 | -0.0066 | 0.0073 | -967.7214 | -947.7807 |
| 1h | box_support − box_any | full | 20 | 2000 | 2199 | 2157 | -0.0921 | -0.1090 | -0.0169 | -0.0369 | 0.0000 | -202.6292 | -235.1595 |
| 1h | box_support − box_any | earlier | 13 | 2000 | 1122 | 1100 | 0.0381 | 0.0129 | -0.0252 | -0.0552 | 0.0008 | 42.7585 | 14.2047 |
| 1h | box_support − box_any | later | 8 | 2000 | 1077 | 1057 | -0.2278 | -0.2359 | -0.0081 | -0.0275 | 0.0105 | -245.3877 | -249.3643 |

B−A 的区间为两臂共同 UTC 月份重抽样 2,000 次（seed=91509），每次按各臂自己的交易数重算均值；描述性，不是盲测。随机超额 p 沿用月块符号置换，不是 B−A 的 p。

| timeframe | full_positive | later_positive | random_excess_positive | p_lt_001 | passed | p_bonferroni_2 |
| --- | --- | --- | --- | --- | --- | --- |
| 15m | False | False | True | False | False | 0.1679 |
| 1h | False | False | True | False | False | 0.9385 |

passed 必须全期与后段净均值均为正、全期随机超额为正且原始 p<0.01；同时列出两个周期的 Bonferroni p 参考。任何研究门通过也不会自动 promote。

## 误删与仓位变化

| timeframe | period | removed | removed_gate_fail | removed_occupancy | removed_losers | removed_winners | removed_ge10r | avoided_loss_r | forgone_profit_r | added | added_net_r | delta_total_r |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 15m | full | 138 | 138 | 0 | 103 | 35 | 0 | 107.1747 | 66.4799 | 1 | 0.9426 | 41.6374 |
| 15m | earlier | 70 | 70 | 0 | 52 | 18 | 0 | 54.2815 | 33.5274 | 1 | 0.9426 | 21.6967 |
| 15m | later | 68 | 68 | 0 | 51 | 17 | 0 | 52.8933 | 32.9525 | 0 | 0.0000 | 19.9407 |
| 1h | full | 43 | 43 | 0 | 25 | 18 | 1 | 23.4980 | 55.0071 | 1 | -1.0212 | -32.5304 |
| 1h | earlier | 23 | 23 | 0 | 10 | 13 | 0 | 9.8356 | 37.3681 | 1 | -1.0212 | -28.5538 |
| 1h | later | 20 | 20 | 0 | 15 | 5 | 1 | 13.6624 | 17.6390 | 0 | 0.0000 | -3.9766 |

removed_gate_fail 是直接支撑拒绝；removed_occupancy 是虽然通过支撑、但被新交易占仓而消失。forgone_profit_r 是误删盈利总和，avoided_loss_r 是少亏的绝对值。added 是完整重放才出现的交易。逐周期、逐段验证：总净 R 差 = 新增交易净 R − 消失交易净 R；共同交易 14 字段和随机对照均不变。

## 复现核对

| comparison | left | right | shared | only_left | only_right | diff_signal_i | diff_entry_i | diff_entry_time | diff_entry_price | diff_initial_stop | diff_initial_risk | diff_exit_i | diff_exit_time | diff_exit_price | diff_exit_reason | diff_net_r | diff_gross_r | diff_censored | diff_mfe_r | diff_initial_risk_frac | diff_gross_return | diff_net_return | diff_matched | diff_control_net_r | diff_control_net_return | diff_status | passed |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| original_vs_baseline | 9287 | 9287 | 9287 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | True |
| shared_baseline_vs_support | 9287 | 9108 | 9106 | 181 | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | True |

代码提交 `626a4b364adb477d600f4a52735341c9b7e29c69`；run identity `23964e9b6d5ed9090b29d1d87b7f1b072a6cf666b8ae429673b87e344f6bdc33`。638 个回执逐个核对输出 SHA，基线成交和状态集合与原 V11.1 框内回测相同。

验证：118 项策略与边界测试、4 项报告完整性测试通过。注册表套件另有 12 通过、4 失败，失败为本轮之前已存在的记录缺少 source_commit；本次新记录单独校验通过。详情见实验目录 validation.json。

## 复现命令

在仓库根目录执行；需保留原638份5m档案及旧基线账本（哈希见 summary.json 和 identity.json）。

```bash
.venv/bin/python -m pytest tests/evaluation/test_spike_v112_support.py -q
.venv/bin/python -m yoyo.evaluation.spike_v112_support_study --output experiments/active/exp-spike-v112-support-20260919-v1/results/run_v1 --workers 8
.venv/bin/python -m yoyo.evaluation.spike_v112_support_report --run experiments/active/exp-spike-v112-support-20260919-v1/results/run_v1 --output experiments/active/exp-spike-v112-support-20260919-v1/statistics/run_v1
.venv/bin/python scripts/md_to_html.py analysis/p1_spike_v112_support_20260919.md --out-dir analysis/html
```

第一次运行生成输出；再次运行验证同身份回执后复用。改变任一输入/依赖必须使用新输出目录，不能覆盖旧 run 身份。

## 风险与诚实声明

这是已看过数据上的逻辑假设检验，不能称为样本外发现。币池采用后来仍挂牌的合约，存活偏差仍在；跨币与行情相关，638 币不等于638个独立样本。日期聚合、暖机和缺口沿用旧实现以保持一致，并非已做 TradingView 逐根认证。次根开盘撮合，没有额外滑点、资金费、盘口容量或合约精度约束；0.2% 是原成本假设。合计 R、事件回撤及跨币同时持仓不是共享资金账户收益。未平仓/断档截尾单单列，不混进已平仓净收益。没有参数搜索或选择最优周期，Pine、监控与实盘配置未改。

## 下一步

先根据本轮全期、后段和误删赢家判断此条件是否值得保留；失败也保留完整记录。参数搜索不是本轮任务。若以后改 TP/SL、成本或生产默认配置，仍需 Owner 决策。

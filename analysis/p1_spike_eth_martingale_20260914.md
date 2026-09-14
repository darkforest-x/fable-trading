# ETH V8 止损后倍投：1000 USDT 有限资金研究

本地交付：[MD](/Users/zhangzc/fable-trading/analysis/p1_spike_eth_martingale_20260914.md) · [HTML](/Users/zhangzc/fable-trading/analysis/html/p1_spike_eth_martingale_20260914.html) · [完整开发搜索 CSV](/Users/zhangzc/fable-trading/experiments/active/exp-spike-eth-martingale-20260914-v1/results/pre/development_search.csv)。

## 先看结论

开发搜索共比较 40 次，涉及 34 个周期×参数组合（去掉周期后为 25 组不同参数）；候选期末余额最高 923.32U。所有开发候选期末余额是否低于 1000U：是。
复用验证 martingale：全部亏损（ETH_3m_OKX 利润 -247.28U；ETH_5m_Binance 利润 -166.09U）。
continuous_pre 连续账户：ETH_3m_OKX 期末 724.82U、接受 11、拒绝 1443；ETH_5m_Binance 期末 792.28U、接受 3、拒绝 828。
本轮未找到可推荐的持续盈利倍投方案；以下仅为受限开发搜索中的开发冠军，不构成可用策略。
反例边界：ETH_5m_Binance 在 2026年1–4月 preholdout 单窗 martingale 利润 185.38U；同窗 fixed 期末 1327.12U，更高；实际最高执行层级为0，该盈利窗口没有执行翻倍；它不能推翻连续账户与其他窗口的结论。
冻结后唯一授权的最终 holdout：ETH_3m_OKX 期末 876.96U、接受 7、拒绝 105。

本报告只渲染已冻结的研究产物：没有重新计算 V8 信号、OHLCV 或政策搜索。开发冠军由开发期四阶段单变量坐标搜索产生；验证、预 holdout 与最终 holdout 均只评估该冻结选择，不预设结果正负。

开发冠军只是在受限开发搜索中期末余额最高的政策，不能等同于可用的“最佳方案”。特别是容量不足会导致大量 V8 候选被拒绝；账户参与率必须与余额一起判断，不能只看少数接受交易的结果。

## 开发冠军与冻结选择

选择只使用开发期账户期末余额；平手按较低底注、层数与容量优先。它是有限坐标搜索的开发冠军，不是全局最优证明。

| stream | 开发阶段 | 底注(U) | 最大层级 | 复位 | 容量上限 | 该步冠军期末余额(U) |
| --- | --- | --- | --- | --- | --- | --- |
| ETH_3m_OKX | 1_levels | 10.00 | 4 | win | 10.00 | 799.64 |
| ETH_3m_OKX | 2_reset | 10.00 | 4 | win | 10.00 | 799.64 |
| ETH_3m_OKX | 3_base_risk | 20.00 | 4 | win | 10.00 | 817.68 |
| ETH_3m_OKX | 4_capacity | 20.00 | 4 | win | 5.00 | 923.32 |
| ETH_5m_Binance | 1_levels | 10.00 | 4 | win | 10.00 | 435.42 |
| ETH_5m_Binance | 2_reset | 10.00 | 4 | recovery | 10.00 | 571.98 |
| ETH_5m_Binance | 3_base_risk | 50.00 | 4 | recovery | 10.00 | 747.22 |
| ETH_5m_Binance | 4_capacity | 50.00 | 4 | recovery | 3.00 | 885.13 |

最终冻结政策：

| stream | 底注(U) | 最大层级 | 复位 | 容量上限 |
| --- | --- | --- | --- | --- |
| ETH_3m_OKX | 20.00 | 4 | win | 5.00 |
| ETH_5m_Binance | 50.00 | 4 | recovery | 3.00 |

开发冠军的容量参与情况（动态读取正式账本摘要）：

| stream | 接受 | 拒绝 | 接受率 |
| --- | --- | --- | --- |
| ETH_3m_OKX | 2 | 742 | 0.27% |
| ETH_5m_Binance | 2 | 437 | 0.46% |

1/2/4 层是计划初始止损金额序列，底注为固定 USDT，不是保证金或 ETH 数量：notional = base_risk_usdt × 2**level / initial_risk_frac。触发仅为净亏损且退出原因含 stop；非 stop 亏损保持层级。触顶触发亏损会认亏、记录 capped_cycle_reset 并回到 level 0，绝不会抹掉账户损失。win 是任意净盈利复位，recovery 仅在本轮累计净 PnL 回本后复位。

## 正式窗口账户结果

matched_control_pnl、matched_delta 与 matched_p 来自实际接受交易按计划风险金额加权的同 ETH、同 UTC 月、同此前 120 bar 波动桶匹配事件。它们是配对反事实，不是可交易随机账户。

### 开发

| stream | 账户 | 期末余额(U) | 利润(U) | 最大已实现回撤 | 接受 | 拒绝 | 最高实际杠杆 | 窗口末标记 | 归零 | 账户自然PF | matched_control_pnl(U) | matched_delta(U) | matched_p |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ETH_3m_OKX | martingale | 923.32 | -76.68 | 7.67% | 2 | 742 | 4.95 | 0 | 否 | 0.00 | -0.82 | -75.86 | 0.5055 |
| ETH_3m_OKX | fixed | 286.32 | -713.68 | 71.37% | 52 | 692 | 4.95 | 0 | 否 | 0.23 | -671.95 | -41.73 | 0.8233 |
| ETH_5m_Binance | martingale | 885.13 | -114.87 | 11.49% | 2 | 437 | 2.98 | 0 | 否 | 0.00 | -10.28 | -104.59 | 1.0000 |
| ETH_5m_Binance | fixed | 799.93 | -200.07 | 20.01% | 6 | 433 | 2.98 | 0 | 否 | 0.03 | -51.14 | -148.93 | 0.5055 |

### 复用验证

| stream | 账户 | 期末余额(U) | 利润(U) | 最大已实现回撤 | 接受 | 拒绝 | 最高实际杠杆 | 窗口末标记 | 归零 | 账户自然PF | matched_control_pnl(U) | matched_delta(U) | matched_p |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ETH_3m_OKX | martingale | 752.72 | -247.28 | 30.09% | 14 | 511 | 4.77 | 0 | 否 | 0.51 | -176.87 | -70.41 | 0.4901 |
| ETH_3m_OKX | fixed | 381.03 | -618.97 | 69.20% | 97 | 428 | 4.94 | 0 | 否 | 0.57 | -624.06 | 5.09 | 0.9807 |
| ETH_5m_Binance | martingale | 833.91 | -166.09 | 16.61% | 5 | 296 | 2.95 | 0 | 否 | 0.00 | 7.07 | -173.16 | 0.1272 |
| ETH_5m_Binance | fixed | 726.91 | -273.09 | 27.31% | 8 | 293 | 2.84 | 0 | 否 | 0.00 | -79.27 | -193.81 | 0.1823 |

### 预 holdout

| stream | 账户 | 期末余额(U) | 利润(U) | 最大已实现回撤 | 接受 | 拒绝 | 最高实际杠杆 | 窗口末标记 | 归零 | 账户自然PF | matched_control_pnl(U) | matched_delta(U) | matched_p |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ETH_3m_OKX | martingale | 862.63 | -137.37 | 13.74% | 4 | 181 | 4.37 | 0 | 否 | 0.00 | 131.33 | -268.69 | 1.0000 |
| ETH_3m_OKX | fixed | 727.33 | -272.67 | 30.87% | 68 | 117 | 4.93 | 0 | 否 | 0.75 | -357.26 | 84.60 | 1.0000 |
| ETH_5m_Binance | martingale | 1185.38 | 185.38 | 6.76% | 4 | 87 | 2.75 | 0 | 否 | 2.61 | 9.42 | 175.96 | 1.0000 |
| ETH_5m_Binance | fixed | 1327.12 | 327.12 | 9.73% | 7 | 84 | 2.86 | 0 | 否 | 3.13 | -101.03 | 428.15 | 0.2498 |

### 开发至预 holdout 连续账户诊断

| stream | 账户 | 期末余额(U) | 利润(U) | 最大已实现回撤 | 接受 | 拒绝 | 最高实际杠杆 | 窗口末标记 | 归零 | 账户自然PF | matched_control_pnl(U) | matched_delta(U) | matched_p |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ETH_3m_OKX | martingale | 724.82 | -275.18 | 30.89% | 11 | 1443 | 4.95 | 0 | 否 | 0.42 | -37.54 | -237.63 | 0.0625 |
| ETH_3m_OKX | fixed | 387.32 | -612.68 | 75.79% | 87 | 1367 | 4.95 | 0 | 否 | 0.52 | -567.65 | -45.03 | 0.8807 |
| ETH_5m_Binance | martingale | 792.28 | -207.72 | 20.77% | 3 | 828 | 2.98 | 0 | 否 | 0.00 | -7.19 | -200.53 | 0.5055 |
| ETH_5m_Binance | fixed | 584.66 | -415.34 | 41.53% | 13 | 818 | 2.98 | 0 | 否 | 0.02 | -108.09 | -307.25 | 0.0644 |

### 最终 holdout（冻结后授权第 1 次）

| stream | 账户 | 期末余额(U) | 利润(U) | 最大已实现回撤 | 接受 | 拒绝 | 最高实际杠杆 | 窗口末标记 | 归零 | 账户自然PF | matched_control_pnl(U) | matched_delta(U) | matched_p |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ETH_3m_OKX | martingale | 876.96 | -123.04 | 12.30% | 7 | 105 | 4.65 | 1 | 否 | 0.26 | 29.58 | -152.62 | 1.0000 |
| ETH_3m_OKX | fixed | 974.92 | -25.08 | 23.33% | 46 | 66 | 4.88 | 1 | 否 | 0.97 | -320.94 | 298.28 | 0.2483 |

最终 holdout 只允许 OKX ETH 3m；Binance 5m 源截至 2026-05-01，没有 holdout 数据。

## 数据统计与自然交易参考

以下 refwinrate 与参考机会池 PF 来自全部自然平仓机会，未施加现金账户的拒单/接受约束，因此不是账户成交 PF；账户自然 PF 已单列在正式账户表。窗口末标记不混入任一胜率或 PF。AUC 不适用：本研究没有训练预测器或预测分数。

| stream | 窗口 | 候选 | opportunity | 自然平仓 | 自然胜 | refwinrate | 参考机会池自然PF | 窗口末标记 | 时间范围 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ETH_3m_OKX | development | 923 | 744 | 744 | 167 | 22.45% | 0.37 | 0 | 2023-08-03T04:30:00+00:00 至 2024-12-28T10:09:00+00:00 |
| ETH_3m_OKX | validation | 653 | 525 | 525 | 138 | 26.29% | 0.51 | 0 | 2025-01-01T04:36:00+00:00 至 2025-12-31T12:48:00+00:00 |
| ETH_3m_OKX | preholdout | 233 | 185 | 185 | 49 | 26.49% | 0.60 | 0 | 2026-01-01T06:51:00+00:00 至 2026-04-30T23:36:00+00:00 |
| ETH_3m_OKX | continuous_pre | 1809 | 1454 | 1454 | 354 | 24.35% | 0.45 | 0 | 2023-08-03T04:30:00+00:00 至 2026-04-30T23:36:00+00:00 |
| ETH_5m_Binance | development | 558 | 439 | 439 | 98 | 22.32% | 0.39 | 0 | 2023-08-03T04:35:00+00:00 至 2024-12-30T01:25:00+00:00 |
| ETH_5m_Binance | validation | 368 | 301 | 301 | 83 | 27.57% | 0.72 | 0 | 2025-01-01T08:35:00+00:00 至 2025-12-31T14:50:00+00:00 |
| ETH_5m_Binance | preholdout | 114 | 91 | 91 | 28 | 30.77% | 1.15 | 0 | 2026-01-01T11:25:00+00:00 至 2026-04-28T21:45:00+00:00 |
| ETH_5m_Binance | continuous_pre | 1040 | 831 | 831 | 209 | 25.15% | 0.58 | 0 | 2023-08-03T04:35:00+00:00 至 2026-04-28T21:45:00+00:00 |
| ETH_3m_OKX | holdout | 150 | 112 | 111 | 36 | 32.43% | 0.76 | 1 | 2026-05-05T11:39:00+00:00 至 2026-07-30T11:09:00+00:00 |

既有原始 baseline：3m 111 笔均 −0.237R、5m 693 笔均 −0.275R，来自不同历史样本，不能与本报告各窗口从 1000U 重启的账户百分比直接比较。

## 最高实际风险 10% 的交易描述

仅将账户账本中 accepted 且非 censored 的交易与 opportunities 合并；按实际 risk_dollars=notional×initial_risk_frac 排序，取最高 10%。这描述仓位分配，并非训练排序或 alpha 证明。风险金额并列时按 trade_id 稳定取样；固定风险基线的最高10%只是一组并列样本，没有风险排序含义。

| stream | 窗口 | 账户 | 最高风险10% | 其余 | 高风险平均毛PnL(U) | 高风险平均净PnL(U) | 高风险平均净R | 其余平均毛PnL(U) | 其余平均净PnL(U) | 其余平均净R | 匹配随机平均净R | 配对美元差(U) | 匹配数 | 解释 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ETH_3m_OKX | development | martingale | 1 | 1 | -40.00 | -46.78 | -1.17 | -20.00 | -29.89 | -1.49 | 0.09 | -50.29 | 1 | 仅为同币同月同波动桶匹配事件描述，不是可交易随机账户 |
| ETH_3m_OKX | development | fixed | 6 | 46 | -0.96 | -7.55 | -0.38 | -9.74 | -14.53 | -0.73 | -0.82 | 53.26 | 6 | 仅为同币同月同波动桶匹配事件描述，不是可交易随机账户 |
| ETH_3m_OKX | validation | martingale | 2 | 12 | -120.00 | -127.72 | -1.07 | 7.60 | 0.68 | -0.26 | -0.20 | -155.78 | 2 | 仅为同币同月同波动桶匹配事件描述，不是可交易随机账户 |
| ETH_3m_OKX | validation | fixed | 10 | 87 | 9.63 | 5.41 | 0.27 | -3.31 | -7.74 | -0.39 | -0.49 | 151.51 | 10 | 仅为同币同月同波动桶匹配事件描述，不是可交易随机账户 |
| ETH_3m_OKX | preholdout | martingale | 1 | 3 | -32.38 | -38.99 | -0.97 | -25.24 | -32.79 | -1.26 | 5.06 | -241.23 | 1 | 仅为同币同月同波动桶匹配事件描述，不是可交易随机账户 |
| ETH_3m_OKX | preholdout | fixed | 7 | 61 | -6.93 | -12.88 | -0.64 | 2.74 | -2.99 | -0.15 | -0.38 | -36.90 | 7 | 仅为同币同月同波动桶匹配事件描述，不是可交易随机账户 |
| ETH_3m_OKX | continuous_pre | martingale | 2 | 9 | -120.00 | -127.72 | -1.07 | 4.72 | -2.19 | -0.49 | -0.20 | -155.78 | 2 | 仅为同币同月同波动桶匹配事件描述，不是可交易随机账户 |
| ETH_3m_OKX | continuous_pre | fixed | 9 | 78 | 1.15 | -3.69 | -0.18 | -3.44 | -7.43 | -0.37 | -0.31 | 21.93 | 9 | 仅为同币同月同波动桶匹配事件描述，不是可交易随机账户 |
| ETH_5m_Binance | development | martingale | 1 | 1 | -54.07 | -58.91 | -0.59 | -50.00 | -55.96 | -1.12 | -0.75 | 15.61 | 1 | 仅为同币同月同波动桶匹配事件描述，不是可交易随机账户 |
| ETH_5m_Binance | development | fixed | 1 | 5 | 10.66 | 6.81 | 0.14 | -36.86 | -41.37 | -0.83 | -0.48 | 30.74 | 1 | 仅为同币同月同波动桶匹配事件描述，不是可交易随机账户 |
| ETH_5m_Binance | validation | martingale | 1 | 4 | -27.07 | -32.19 | -0.32 | -29.98 | -33.47 | -0.67 | 0.06 | -38.52 | 1 | 仅为同币同月同波动桶匹配事件描述，不是可交易随机账户 |
| ETH_5m_Binance | validation | fixed | 1 | 7 | -27.77 | -33.46 | -0.67 | -31.09 | -34.23 | -0.68 | -0.64 | -1.36 | 1 | 仅为同币同月同波动桶匹配事件描述，不是可交易随机账户 |
| ETH_5m_Binance | preholdout | martingale | 1 | 3 | -23.65 | -29.36 | -0.59 | 76.69 | 71.58 | 1.43 | -0.52 | -3.12 | 1 | 仅为同币同月同波动桶匹配事件描述，不是可交易随机账户 |
| ETH_5m_Binance | preholdout | fixed | 1 | 6 | -23.65 | -29.36 | -0.59 | 64.82 | 59.41 | 1.19 | -0.52 | -3.12 | 1 | 仅为同币同月同波动桶匹配事件描述，不是可交易随机账户 |
| ETH_5m_Binance | continuous_pre | martingale | 1 | 2 | -54.07 | -58.91 | -0.59 | -68.83 | -74.40 | -1.02 | -0.75 | 15.61 | 1 | 仅为同币同月同波动桶匹配事件描述，不是可交易随机账户 |
| ETH_5m_Binance | continuous_pre | fixed | 2 | 11 | -0.64 | -4.37 | -0.09 | -33.28 | -36.96 | -0.74 | -0.56 | 46.92 | 2 | 仅为同币同月同波动桶匹配事件描述，不是可交易随机账户 |
| ETH_3m_OKX | holdout | martingale | 1 | 6 | -40.00 | -47.88 | -1.20 | -4.87 | -12.53 | -0.66 | 1.22 | -96.52 | 1 | 仅为同币同月同波动桶匹配事件描述，不是可交易随机账户 |
| ETH_3m_OKX | holdout | fixed | 5 | 40 | 23.18 | 16.88 | 0.84 | 3.79 | -2.68 | -0.13 | -0.59 | 143.17 | 5 | 仅为同币同月同波动桶匹配事件描述，不是可交易随机账户 |

## 连续账户诊断图

![ETH_3m_OKX_continuous_pre_equity](/Users/zhangzc/fable-trading/experiments/active/exp-spike-eth-martingale-20260914-v1/results/report_assets/ETH_3m_OKX_continuous_pre_equity.png)

![ETH_5m_Binance_continuous_pre_equity](/Users/zhangzc/fable-trading/experiments/active/exp-spike-eth-martingale-20260914-v1/results/report_assets/ETH_5m_Binance_continuous_pre_equity.png)

曲线使用已实现退出或窗口末标记时点，不是逐 bar 浮动权益；每个正式窗口各自从 1000U 空仓重启，continuous_pre 才显示开发起至预 holdout 的连续诊断。

## 风险与诚实声明

- 现金账本没有真实 mark price、持仓内最大浮亏、历史分层维持保证金、资金费、盘口滑点或可执行成交。现金未归零不能证明避免交易所强平；归零也不是历史交易所强平概率。
- 回撤为已实现或窗口末标记口径，不是持仓内最大回撤。容量上限只是模拟入场约束，不能作为实际杠杆建议。
- 容量不足的候选保持层级，并等待原冻结 V8 影子持仓结束后再尝试下一笔；不是拒单后立刻扫描所有空档信号。
- 研究含跨所差异、已被历史研究复用的数据、多次开发选择、有限样本和固定 9 个随机种子。任何看似异常好的结果先按 bug 或泄漏检查，不能自动 promote、部署或称为实盘证据。

## 复现与 holdout 纪律

Owner 原始约束为“1000 承受爆仓”，授权为“冻结方案后使用 holdout 最终评估”。本配置第1次消耗 holdout，授权、冻结选择与消耗收据均保存在实验目录。首次 builder 提交00cb37d879；输入哈希修复ba84ff0a2f；最终选择提交f4aa4530bc在验证及holdout读取之前。首次选择保留为selection.initial.json，修复前后政策和搜索表完全一致。

V8原始确认、压缩门、同方向绳索距离3ATR、次根开盘入场、结构止损、2R后4ATR跟踪及原V6反向退出均冻结。每笔成本固定为名义本金的0.2%往返，入场额外预留同额费用但不重复扣款。开发2023-08-01至2025-01-01，验证为2025全年，预holdout为2026年1至4月；区间右端不含。最大层级1至8、复位win/recovery、底注1/2/5/10/20/50U、模拟容量3/5/10/20依次单字段搜索，没有穷举全部交叉组合。

核验记录见实验目录verification.json：逐笔账本与18行账户摘要对账、产物哈希及13项专项测试。合计113项检查通过、4项仓库注册表检查失败：其他研究的两个记录缺失source_commit；本研究记录单独按契约核验。未把全库检查计为通过。浏览器安全策略阻止本地HTML页面预览，因此仅完成静态结构和图片资源检查，不声称浏览器视觉验收通过。

```bash
# 先确认 builder、内核、测试和计划已提交；prepare 输出不可覆盖
.venv/bin/python -m yoyo.evaluation.spike_eth_martingale_study prepare --phase pre
.venv/bin/python -m yoyo.evaluation.spike_eth_martingale_study select
git add experiments/active/exp-spike-eth-martingale-20260914-v1/selection.json experiments/active/exp-spike-eth-martingale-20260914-v1/results/pre/development_search.csv
git branch --show-current  # 必须为 main
git commit -m 'Freeze ETH martingale development selection'
.venv/bin/python -m yoyo.evaluation.spike_eth_martingale_study evaluate --phase pre
# 仅在 Owner 已授权的冻结配置上执行一次；如需重跑，创建新版本并记录新的曝光，绝不直接重复 holdout
.venv/bin/python -m yoyo.evaluation.spike_eth_martingale_study prepare --phase holdout
.venv/bin/python -m yoyo.evaluation.spike_eth_martingale_study evaluate --phase holdout
.venv/bin/python -m yoyo.evaluation.spike_eth_martingale_report
```

## 下一步选项

本版记为拒绝，不启用。可复用的是有限资金核算与拒单诊断。若继续研究容量不足时重置、不同入场机会或更高容量，须另立配置、先预注册再评估；其中重新使用holdout或修改V8障碍、成本需Owner另行决策，本轮不会凭已有holdout结果继续挑参数。

已归档至[Spike研究记录](https://app.notion.com/p/3db8856479af81bf9c94f5e9f483e621)。

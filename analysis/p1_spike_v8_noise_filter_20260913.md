# SPIKE V8：V7 全量信号降噪与冻结规则回放

V8 只增加一个因果入场门：确认收盘价沿交易方向离六均线绳索边缘超过 3 ATR 时，不再建立新参考。已有持仓仍可被未过滤的反向 V6 确认结束，因此 V8 没有偷偷改变退出规则。Pine 是研究版；本轮未修改生产监控、Bark、ACTIVE、promote 或实盘设置。

## 先看结论

V7 共 **132,593 条确认事件**，其中 **107,238 条形成串行交易**，另有 **25,355 条**因同一交易流已有持仓而没有成为新交易。冻结的 V8 门槛保留 **113,295 条确认**、形成 **95,191 条交易，信号总量减少 14.55%**。

验证段的核心结果如下。账户收益是每个交易所×币种×周期独立账户的均值，不是把 3,531 个流叠成可实盘的共享资金曲线。

| 周期 | V7信号 | V8信号 | 删减 | V7_PF | V8_PF | V7账户收益 | V8账户收益 | V7均值回撤 | V8均值回撤 | 原V7_10R保留 | V8减V7_p |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 30m | 49802 | 42550 | 0.1456 | 0.8827 | 0.8708 | -0.0294 | -0.0289 | 0.1316 | 0.1250 | 0.9175 | 0.6927 |
| 1H | 24594 | 20628 | 0.1613 | 0.9641 | 0.9761 | 0.0017 | 0.0015 | 0.0846 | 0.0798 | 0.9388 | 0.8866 |
| 4H | 6980 | 5978 | 0.1436 | 1.1227 | 1.1534 | 0.0036 | 0.0025 | 0.0371 | 0.0351 | 0.9677 | 0.0625 |

V8 达到了本轮的窄目标：每个周期少约 14%–16% 的信号，原 V7 已实现 10R 大趋势仍保留 91.75%–96.77%，三个周期的平均收盘回撤都下降。它没有证明净收益全面优于 V7：30m PF 由 0.883 降到 0.871；1H 和 4H PF 略升，但同资产配对后的 V8−V7 收益差均未达到 p<0.05。当前应把 V8 看成**追高/追空保护版**，不能宣传成已经找到最赚钱参数。

## V8 规则与时间纪律

V8 完整继承 V7 的 V6 结构确认、BB200 压缩背景、多空信号、收盘确认、次根开盘入场、结构止损和趋势跟随。唯一变化是：多头用 `(close - 六线最高值) / ATR <= 3`，空头用 `(六线最低值 - close) / ATR <= 3`。所有输入均在信号 K 收盘时可知，不回填历史信号。

认证回放覆盖 3531 条 Binance、OKX、Gate 数据流，周期为 30m/1H/4H，窗口为 2024-09-10 至 2026-09-10。候选汇总表只使用 2024-09-10 至 2025-09-10 的开发段，`selected_rule.json` 也先于串行验证回放写入；但独立复核发现，原 `discovery_v1/signal_features.csv.gz` 仍错误携带了 2025-09-10 至 2026-09-10 的验证结果列。报告生成器现要求哈希绑定的勘误并在分析前清空这些列，但已经发生的暴露无法撤销，所以本轮所有验证结论只能作为**非盲描述性证据**。

## 132,593 条信号究竟失败在哪里

下面第一张分类表只统计开发段可用结果；全量 132,593 条确认的交易级失败结构由随后 107,238 笔串行交易及退出表给出。验证段确认的特征文件结果已被报告器主动屏蔽。

| failure_reason | admissions | executed | mean_net_return | mean_net_r |
|---|---|---|---|---|
| initial_stop_later | 20399 | 20399 | -0.0477 | -1.0635 |
| positive_below10r | 13124 | 13124 | 0.0878 | 2.1037 |
| not_executed_occupied | 9757 | 0 | N/A | N/A |
| opposite_signal_loss | 4685 | 4685 | -0.0338 | -0.5360 |
| initial_stop_within3 | 2568 | 2568 | -0.0354 | -1.0790 |
| trailing_giveback_loss | 309 | 309 | -0.0063 | -0.2384 |
| realized_ge10r | 226 | 226 | 0.5883 | 16.2398 |
| cost_dominated_tiny_risk | 147 | 147 | -0.0025 | -10.9165 |
| censored | 2 | 2 | N/A | N/A |

| exit_reason | exits | losses | net_return_sum | net_r_sum |
|---|---|---|---|---|
| initial_stop | 60984 | 60984 | -2612.7150 | -67910.3933 |
| trailing_stop | 30384 | 1126 | 2838.8973 | 70258.0141 |
| opposite_v6_next_open | 15140 | 11974 | -256.3095 | -5410.3539 |
| initial_stop_gap | 129 | 129 | -3.7433 | -209.3479 |
| trailing_stop_gap | 54 | 4 | 6.3367 | 112.5171 |

V7 的 106,691 笔已结束交易中有 74,217 笔净亏损。初始止损占全部已结束交易 57.28%，占全部亏损 82.34%；把亏损的反向确认退出也算上，两类合计解释 98.48% 的亏损。真正的主要问题是大量启动没有形成持续性，以及部分确认时价格已经离均线绳索太远；并非缺少更猛烈的当根成交量。

`not_executed_occupied` 只是已有持仓时出现的候选确认，没有自然交易结果，不能算失败交易。跨交易所同币同时间的信号也不能直接当噪音删除：它们反映同一市场事件，统计推断已按基础资产聚类，前端层可以折叠展示，但交易研究不能把它们伪装成独立样本。

## 为什么没有采用更强的量价和 BB 门槛

| gate | timeframe_min | signals | signals_kept | signal_reduction | event_pf | mean_net_return | realized_10r | realized_10r_kept | realized_10r_retention | mfe_10r_kept |
|---|---|---|---|---|---|---|---|---|---|---|
| baseline | 30 | 30286 | 30286 | 0.0000 | 1.1356 | 0.0031 | 143 | 143 | 1.0000 | 594 |
| gate_efficiency55 | 30 | 30286 | 12723 | 0.5799 | 1.1522 | 0.0038 | 143 | 40 | 0.2797 | 166 |
| gate_bb_rising1 | 30 | 30286 | 17611 | 0.4185 | 1.0818 | 0.0021 | 143 | 57 | 0.3986 | 282 |
| gate_bb_released | 30 | 30286 | 4036 | 0.8667 | 1.1610 | 0.0046 | 143 | 12 | 0.0839 | 45 |
| gate_current_volume15 | 30 | 30286 | 22731 | 0.2495 | 1.1123 | 0.0027 | 143 | 73 | 0.5105 | 358 |
| gate_current_tr15 | 30 | 30286 | 12942 | 0.5727 | 1.0976 | 0.0025 | 143 | 33 | 0.2308 | 148 |
| gate_v1_hard_impulse | 30 | 30286 | 2023 | 0.9332 | 0.9835 | -0.0005 | 143 | 3 | 0.0210 | 16 |
| gate_cost_share25 | 30 | 30286 | 29928 | 0.0118 | 1.1365 | 0.0032 | 143 | 142 | 0.9930 | 585 |
| gate_not_overheated3 | 30 | 30286 | 26088 | 0.1386 | 1.1471 | 0.0032 | 143 | 136 | 0.9510 | 565 |
| baseline | 60 | 15984 | 15984 | 0.0000 | 0.8912 | -0.0035 | 53 | 53 | 1.0000 | 193 |
| gate_efficiency55 | 60 | 15984 | 6640 | 0.5846 | 0.8637 | -0.0049 | 53 | 9 | 0.1698 | 37 |
| gate_bb_rising1 | 60 | 15984 | 9087 | 0.4315 | 0.8470 | -0.0053 | 53 | 26 | 0.4906 | 74 |
| gate_bb_released | 60 | 15984 | 2071 | 0.8704 | 0.8659 | -0.0052 | 53 | 3 | 0.0566 | 10 |
| gate_current_volume15 | 60 | 15984 | 11756 | 0.2645 | 0.8600 | -0.0048 | 53 | 36 | 0.6792 | 111 |
| gate_current_tr15 | 60 | 15984 | 6706 | 0.5805 | 0.9019 | -0.0036 | 53 | 18 | 0.3396 | 51 |
| gate_v1_hard_impulse | 60 | 15984 | 1239 | 0.9225 | 0.8513 | -0.0071 | 53 | 1 | 0.0189 | 3 |
| gate_cost_share25 | 60 | 15984 | 15905 | 0.0049 | 0.8912 | -0.0035 | 53 | 53 | 1.0000 | 191 |
| gate_not_overheated3 | 60 | 15984 | 13701 | 0.1428 | 0.8916 | -0.0033 | 53 | 49 | 0.9245 | 185 |
| baseline | 240 | 4947 | 4947 | 0.0000 | 1.1102 | 0.0068 | 30 | 30 | 1.0000 | 103 |
| gate_efficiency55 | 240 | 4947 | 1911 | 0.6137 | 1.3544 | 0.0245 | 30 | 21 | 0.7000 | 46 |
| gate_bb_rising1 | 240 | 4947 | 2769 | 0.4403 | 0.9837 | -0.0011 | 30 | 13 | 0.4333 | 46 |
| gate_bb_released | 240 | 4947 | 621 | 0.8745 | 0.9327 | -0.0056 | 30 | 4 | 0.1333 | 11 |
| gate_current_volume15 | 240 | 4947 | 3362 | 0.3204 | 1.2133 | 0.0141 | 30 | 25 | 0.8333 | 80 |
| gate_current_tr15 | 240 | 4947 | 2037 | 0.5882 | 0.8626 | -0.0104 | 30 | 5 | 0.1667 | 27 |
| gate_v1_hard_impulse | 240 | 4947 | 353 | 0.9286 | 0.4233 | -0.0689 | 30 | 0 | 0.0000 | 0 |
| gate_cost_share25 | 240 | 4947 | 4930 | 0.0034 | 1.1103 | 0.0069 | 30 | 30 | 1.0000 | 103 |
| gate_not_overheated3 | 240 | 4947 | 4350 | 0.1207 | 1.1682 | 0.0098 | 30 | 28 | 0.9333 | 98 |

成交量≥1.5、TR 扩张≥1.5、BB 当根扩张、三根路径效率≥0.55，以及 V1 的同根 RV≥4 且 TR≥3，都能显著减少信号，却会在至少一个周期误删过多已实现 10R 交易。最严格的 V1 同根量价门甚至删除约 92%–93% 的候选。趋势尾部依赖少数早期入口，所以不能只按普通胜率或当根爆发程度挑门槛。

## V7/V8 确认和交易明细

| period | timeframe_min | v7_admissions | v8_admissions | admission_reduction |
|---|---|---|---|---|
| development | 30 | 30286 | 26088 | 0.1386 |
| development | 60 | 15984 | 13701 | 0.1428 |
| development | 240 | 4947 | 4350 | 0.1207 |
| validation | 30 | 49802 | 42550 | 0.1456 |
| validation | 60 | 24594 | 20628 | 0.1613 |
| validation | 240 | 6980 | 5978 | 0.1436 |

| arm | period | timeframe_min | trade_rows | closed | censored | pf | net_return_sum | win_rate | realized_10r | mfe_10r |
|---|---|---|---|---|---|---|---|---|---|---|
| v7 | development | 30 | 24537 | 24535 | 2 | 1.1356 | 76.0297 | 0.3264 | 143 | 594 |
| v7 | development | 60 | 12813 | 12813 | 0 | 0.8912 | -44.5658 | 0.3158 | 53 | 193 |
| v7 | development | 240 | 4110 | 4110 | 0 | 1.1102 | 28.0867 | 0.3151 | 30 | 103 |
| v7 | validation | 30 | 40540 | 40281 | 259 | 0.8827 | -103.2759 | 0.2854 | 206 | 660 |
| v7 | validation | 60 | 19619 | 19452 | 167 | 0.9641 | -20.8768 | 0.3034 | 98 | 426 |
| v7 | validation | 240 | 5619 | 5500 | 119 | 1.1227 | 37.0684 | 0.3142 | 31 | 137 |
| v8 | development | 30 | 21972 | 21970 | 2 | 1.1476 | 70.7771 | 0.3225 | 136 | 567 |
| v8 | development | 60 | 11422 | 11422 | 0 | 0.8913 | -37.4642 | 0.3139 | 49 | 185 |
| v8 | development | 240 | 3747 | 3747 | 0 | 1.1624 | 35.4346 | 0.3136 | 28 | 99 |
| v8 | validation | 30 | 35829 | 35605 | 224 | 0.8708 | -93.7955 | 0.2798 | 194 | 595 |
| v8 | validation | 60 | 17192 | 17051 | 141 | 0.9761 | -11.1669 | 0.3013 | 95 | 394 |
| v8 | validation | 240 | 5029 | 4951 | 78 | 1.1534 | 38.3192 | 0.3078 | 30 | 128 |

## 独立账户、回撤与同资产配对效应

| arm | period | timeframe_min | streams | mean_net_return | mean_max_drawdown | worst_max_drawdown |
|---|---|---|---|---|---|---|
| v7 | development | 30 | 1144 | 0.0338 | 0.0661 | 0.3124 |
| v7 | development | 60 | 1126 | -0.0041 | 0.0520 | 0.2338 |
| v7 | development | 240 | 1261 | 0.0049 | 0.0265 | 0.1914 |
| v7 | full | 30 | 1144 | 0.0032 | 0.1566 | 0.4174 |
| v7 | full | 60 | 1126 | -0.0023 | 0.1034 | 0.3062 |
| v7 | full | 240 | 1261 | 0.0084 | 0.0505 | 0.2017 |
| v7 | validation | 30 | 1144 | -0.0294 | 0.1316 | 0.3582 |
| v7 | validation | 60 | 1126 | 0.0017 | 0.0846 | 0.2701 |
| v7 | validation | 240 | 1261 | 0.0036 | 0.0371 | 0.1686 |
| v8 | development | 30 | 1144 | 0.0322 | 0.0639 | 0.2798 |
| v8 | development | 60 | 1126 | -0.0035 | 0.0496 | 0.2338 |
| v8 | development | 240 | 1261 | 0.0040 | 0.0250 | 0.1914 |
| v8 | full | 30 | 1144 | 0.0017 | 0.1497 | 0.3873 |
| v8 | full | 60 | 1126 | -0.0020 | 0.0979 | 0.2858 |
| v8 | full | 240 | 1261 | 0.0065 | 0.0480 | 0.1914 |
| v8 | validation | 30 | 1144 | -0.0289 | 0.1250 | 0.3570 |
| v8 | validation | 60 | 1126 | 0.0015 | 0.0798 | 0.2643 |
| v8 | validation | 240 | 1261 | 0.0025 | 0.0351 | 0.1686 |

| period | timeframe_min | delta | low | high | p | assets |
|---|---|---|---|---|---|---|
| development | 30 | -0.0016 | -0.0035 | 0.0003 | 0.0805 | 850 |
| development | 60 | 0.0005 | -0.0009 | 0.0017 | 0.3938 | 829 |
| development | 240 | -0.0009 | -0.0032 | 0.0005 | 0.5657 | 802 |
| validation | 30 | 0.0005 | -0.0020 | 0.0030 | 0.6927 | 850 |
| validation | 60 | -0.0002 | -0.0024 | 0.0019 | 0.8866 | 829 |
| validation | 240 | -0.0011 | -0.0022 | -0.0001 | 0.0625 | 802 |

同流同时间段先配对 V8 与 V7，再按基础资产做 bootstrap 和符号置换，避免 Binance/OKX/Gate 的重复事件虚增显著性。验证段 30m/1H/4H 的 V8−V7 p 值分别为 0.693/0.887/0.063，尚无可靠的整体收益提升。

## 原 V7 大趋势保留率

| period | timeframe_min | baseline_executed | baseline_realized_10r | retained_realized_10r | realized_10r_retention | baseline_mfe_10r | retained_mfe_10r | mfe_10r_retention | removed_losers | missed_realized_winners |
|---|---|---|---|---|---|---|---|---|---|---|
| development | 30 | 24535 | 143 | 136 | 0.9510 | 594 | 565 | 0.9512 | 1792 | 7 |
| development | 60 | 12813 | 53 | 49 | 0.9245 | 193 | 185 | 0.9585 | 1009 | 4 |
| development | 240 | 4110 | 30 | 28 | 0.9333 | 103 | 98 | 0.9515 | 274 | 2 |
| validation | 30 | 40281 | 206 | 189 | 0.9175 | 660 | 583 | 0.8833 | 3494 | 17 |
| validation | 60 | 19452 | 98 | 92 | 0.9388 | 426 | 384 | 0.9014 | 1825 | 6 |
| validation | 240 | 5500 | 31 | 30 | 0.9677 | 137 | 128 | 0.9343 | 398 | 1 |

`realized_10r` 表示原 V7 交易最终净结果确实达到 10R；`mfe_10r` 只是持仓路径中曾到达 10R，不能冒充实际落袋收益。这里的保留率按原 V7 交易逐笔判断，避免 V8 改变持仓占用后造成分母漂移。

## 行情阶段比单根指标更重要

| month | timeframe_min | closed_v7 | pf_v7 | net_r_sum_v7 | realized_10r_v7 | closed_v8 | pf_v8 | net_r_sum_v8 | realized_10r_v8 |
|---|---|---|---|---|---|---|---|---|---|
| 2025-09 | 30 | 1676 | 0.9707 | -374.8492 | 6 | 1528 | 0.8869 | -296.9189 | 6 |
| 2025-09 | 60 | 412 | 1.2328 | -29.0107 | 1 | 361 | 1.2503 | -30.8974 | 1 |
| 2025-09 | 240 | 838 | 1.3797 | 50.3668 | 2 | 730 | 1.0561 | -82.7616 | 2 |
| 2025-10 | 30 | 2918 | 0.7267 | -704.0419 | 11 | 2466 | 0.7118 | -606.4424 | 12 |
| 2025-10 | 60 | 1730 | 1.5536 | 392.9813 | 11 | 1566 | 1.6408 | 438.4402 | 11 |
| 2025-10 | 240 | 108 | 1.5936 | 97.0110 | 0 | 93 | 3.0903 | 98.4170 | 0 |
| 2025-11 | 30 | 3140 | 1.1114 | 158.5974 | 11 | 2875 | 1.1399 | 181.5462 | 11 |
| 2025-11 | 60 | 1024 | 1.0507 | 178.0238 | 2 | 967 | 1.0841 | 189.5682 | 2 |
| 2025-11 | 240 | 54 | 1.1538 | 16.4842 | 0 | 46 | 1.3308 | 16.6895 | 0 |
| 2025-12 | 30 | 3445 | 0.5403 | -1280.0832 | 10 | 2880 | 0.5881 | -949.2241 | 10 |
| 2025-12 | 60 | 2252 | 0.6677 | -668.6177 | 5 | 1867 | 0.7777 | -450.9400 | 4 |
| 2025-12 | 240 | 286 | 0.4503 | -90.7375 | 2 | 258 | 0.3909 | -80.7235 | 2 |
| 2026-01 | 30 | 2767 | 0.7964 | -377.8075 | 4 | 2432 | 0.7585 | -335.4245 | 4 |
| 2026-01 | 60 | 1193 | 1.9276 | 814.5739 | 13 | 981 | 1.8240 | 624.2642 | 11 |
| 2026-01 | 240 | 993 | 1.4702 | 263.3446 | 0 | 899 | 1.5993 | 253.5371 | 0 |
| 2026-02 | 30 | 2315 | 0.9179 | -126.2345 | 8 | 2006 | 0.9206 | 2.3192 | 7 |
| 2026-02 | 60 | 1370 | 0.6320 | -493.0974 | 6 | 1161 | 0.7345 | -322.9310 | 6 |
| 2026-02 | 240 | 65 | 1.0309 | -21.6229 | 0 | 61 | 0.8522 | -26.1391 | 0 |
| 2026-03 | 30 | 3691 | 0.8982 | -71.1561 | 17 | 3430 | 0.9077 | -40.5283 | 16 |
| 2026-03 | 60 | 1693 | 0.7925 | -223.5408 | 7 | 1505 | 0.7556 | -194.1698 | 7 |
| 2026-03 | 240 | 791 | 0.5920 | -267.4064 | 4 | 745 | 0.6162 | -228.0423 | 4 |
| 2026-04 | 30 | 3592 | 1.0860 | -251.7730 | 20 | 3199 | 1.0973 | -189.9575 | 18 |
| 2026-04 | 60 | 2152 | 0.8789 | -467.6428 | 11 | 1924 | 0.7626 | -498.3217 | 10 |
| 2026-04 | 240 | 477 | 1.0685 | -32.9424 | 4 | 431 | 1.0041 | -46.8357 | 4 |
| 2026-05 | 30 | 3409 | 0.8349 | -672.1568 | 4 | 3071 | 0.7540 | -679.8364 | 4 |
| 2026-05 | 60 | 1503 | 1.3768 | 340.7808 | 12 | 1346 | 1.3029 | 283.1065 | 12 |
| 2026-05 | 240 | 464 | 2.3917 | 277.7572 | 6 | 426 | 2.6261 | 271.4756 | 6 |
| 2026-06 | 30 | 3760 | 0.9157 | -583.4498 | 26 | 3270 | 0.8520 | -606.9635 | 27 |
| 2026-06 | 60 | 1355 | 0.5952 | -520.7845 | 3 | 1182 | 0.6697 | -431.5365 | 4 |
| 2026-06 | 240 | 88 | 0.4257 | -26.2023 | 1 | 81 | 0.5235 | -19.7582 | 1 |
| 2026-07 | 30 | 3861 | 0.8655 | -665.5069 | 10 | 3425 | 0.8701 | -590.0163 | 10 |
| 2026-07 | 60 | 2536 | 0.6941 | -679.1437 | 5 | 2253 | 0.6617 | -611.0196 | 5 |
| 2026-07 | 240 | 753 | 0.6815 | -190.0670 | 4 | 681 | 0.7729 | -166.2529 | 3 |
| 2026-08 | 30 | 4364 | 1.1262 | 451.1184 | 72 | 3821 | 1.0557 | 290.9446 | 62 |
| 2026-08 | 60 | 1727 | 1.4005 | 631.3752 | 21 | 1494 | 1.2680 | 516.2366 | 21 |
| 2026-08 | 240 | 550 | 1.2245 | 140.5781 | 8 | 469 | 1.4457 | 174.9489 | 8 |
| 2026-09 | 30 | 1343 | 0.5902 | -512.1779 | 7 | 1202 | 0.6043 | -470.1318 | 7 |
| 2026-09 | 60 | 505 | 0.4777 | -235.7274 | 1 | 444 | 0.5537 | -185.8141 | 1 |
| 2026-09 | 240 | 33 | 0.0554 | -31.4127 | 0 | 31 | 0.0636 | -29.3693 | 0 |

月度结果发生明显翻转。以用户关注的 2026-08 普涨/爆发阶段为例：30m V7 451.1R → V8 290.9R; 60m V7 631.4R → V8 516.2R; 240m V7 140.6R → V8 174.9R。同一套规则在其他月份经常为负，说明 V7 的主要噪音来源之一是**市场状态不适配**。这张表是事件级描述，跨周期和跨交易所存在相关性，不能把 net R 相加当真实账户收益。下一轮最值得单独预注册的是收盘时可知的全市场广度/波动扩散门，而不是继续抬高单币成交量阈值。

## 多空、交易所与匹配随机对照

| period | timeframe_min | side | venue | v7_admissions | v8_admissions | admission_reduction |
|---|---|---|---|---|---|---|
| development | 30 | -1 | binance | 10517 | 9072 | 0.1374 |
| development | 30 | -1 | okx | 4678 | 3995 | 0.1460 |
| development | 30 | 1 | binance | 10364 | 8931 | 0.1383 |
| development | 30 | 1 | okx | 4727 | 4090 | 0.1348 |
| development | 60 | -1 | binance | 5149 | 4488 | 0.1284 |
| development | 60 | -1 | gate | 1 | 1 | 0.0000 |
| development | 60 | -1 | okx | 2274 | 1974 | 0.1319 |
| development | 60 | 1 | binance | 5849 | 4977 | 0.1491 |
| development | 60 | 1 | gate | 1 | 1 | 0.0000 |
| development | 60 | 1 | okx | 2710 | 2260 | 0.1661 |
| development | 240 | -1 | binance | 1149 | 1070 | 0.0688 |
| development | 240 | -1 | gate | 725 | 654 | 0.0979 |
| development | 240 | -1 | okx | 556 | 515 | 0.0737 |
| development | 240 | 1 | binance | 1207 | 1019 | 0.1558 |
| development | 240 | 1 | gate | 733 | 601 | 0.1801 |
| development | 240 | 1 | okx | 577 | 491 | 0.1490 |
| validation | 30 | -1 | binance | 15167 | 13263 | 0.1255 |
| validation | 30 | -1 | gate | 393 | 324 | 0.1756 |
| validation | 30 | -1 | okx | 8931 | 7620 | 0.1468 |
| validation | 30 | 1 | binance | 15692 | 13347 | 0.1494 |
| validation | 30 | 1 | gate | 381 | 303 | 0.2047 |
| validation | 30 | 1 | okx | 9238 | 7693 | 0.1672 |
| validation | 60 | -1 | binance | 7180 | 6130 | 0.1462 |
| validation | 60 | -1 | gate | 461 | 386 | 0.1627 |
| validation | 60 | -1 | okx | 4108 | 3379 | 0.1775 |
| validation | 60 | 1 | binance | 7909 | 6663 | 0.1575 |
| validation | 60 | 1 | gate | 501 | 410 | 0.1816 |
| validation | 60 | 1 | okx | 4435 | 3660 | 0.1747 |
| validation | 240 | -1 | binance | 1519 | 1359 | 0.1053 |
| validation | 240 | -1 | gate | 753 | 664 | 0.1182 |
| validation | 240 | -1 | okx | 827 | 741 | 0.1040 |
| validation | 240 | 1 | binance | 1987 | 1648 | 0.1706 |
| validation | 240 | 1 | gate | 828 | 669 | 0.1920 |
| validation | 240 | 1 | okx | 1066 | 897 | 0.1585 |

| arm | period | timeframe_min | side | trade_rows | pf | win_rate | realized_10r |
|---|---|---|---|---|---|---|---|
| v7 | development | 30 | -1 | 12274 | 1.2346 | 0.3464 | 35 |
| v7 | development | 30 | 1 | 12263 | 1.0457 | 0.3065 | 108 |
| v7 | development | 60 | -1 | 6055 | 0.8472 | 0.3070 | 2 |
| v7 | development | 60 | 1 | 6758 | 0.9308 | 0.3236 | 51 |
| v7 | development | 240 | -1 | 2027 | 0.5560 | 0.2348 | 0 |
| v7 | development | 240 | 1 | 2083 | 1.7526 | 0.3932 | 30 |
| v7 | validation | 30 | -1 | 20204 | 0.9193 | 0.3014 | 47 |
| v7 | validation | 30 | 1 | 20336 | 0.8508 | 0.2696 | 159 |
| v7 | validation | 60 | -1 | 9410 | 1.1331 | 0.3458 | 25 |
| v7 | validation | 60 | 1 | 10209 | 0.8284 | 0.2645 | 73 |
| v7 | validation | 240 | -1 | 2595 | 1.6435 | 0.3869 | 8 |
| v7 | validation | 240 | 1 | 3024 | 0.7608 | 0.2503 | 23 |
| v8 | development | 30 | -1 | 11025 | 1.2315 | 0.3393 | 34 |
| v8 | development | 30 | 1 | 10947 | 1.0684 | 0.3056 | 102 |
| v8 | development | 60 | -1 | 5474 | 0.8054 | 0.2978 | 2 |
| v8 | development | 60 | 1 | 5948 | 0.9761 | 0.3287 | 47 |
| v8 | development | 240 | -1 | 1899 | 0.5831 | 0.2364 | 0 |
| v8 | development | 240 | 1 | 1848 | 1.9013 | 0.3929 | 28 |
| v8 | validation | 30 | -1 | 18043 | 0.8962 | 0.2950 | 47 |
| v8 | validation | 30 | 1 | 17786 | 0.8472 | 0.2646 | 147 |
| v8 | validation | 60 | -1 | 8288 | 1.2210 | 0.3484 | 26 |
| v8 | validation | 60 | 1 | 8904 | 0.7789 | 0.2578 | 69 |
| v8 | validation | 240 | -1 | 2347 | 1.6370 | 0.3752 | 8 |
| v8 | validation | 240 | 1 | 2682 | 0.8077 | 0.2481 | 22 |

| arm | period | timeframe_min | venue | trade_rows | pf | win_rate |
|---|---|---|---|---|---|---|
| v7 | development | 30 | binance | 16959 | 1.1131 | 0.3238 |
| v7 | development | 30 | okx | 7578 | 1.1905 | 0.3324 |
| v7 | development | 60 | binance | 8816 | 0.8737 | 0.3106 |
| v7 | development | 60 | gate | 2 | 2.7158 | 0.5000 |
| v7 | development | 60 | okx | 3995 | 0.9329 | 0.3272 |
| v7 | development | 240 | binance | 1983 | 1.0007 | 0.3011 |
| v7 | development | 240 | gate | 1184 | 1.1268 | 0.3150 |
| v7 | development | 240 | okx | 943 | 1.3497 | 0.3446 |
| v7 | validation | 30 | binance | 25257 | 0.8861 | 0.2839 |
| v7 | validation | 30 | gate | 611 | 1.1177 | 0.3213 |
| v7 | validation | 30 | okx | 14672 | 0.8637 | 0.2864 |
| v7 | validation | 60 | binance | 12088 | 0.9678 | 0.3020 |
| v7 | validation | 60 | gate | 737 | 0.8732 | 0.3133 |
| v7 | validation | 60 | okx | 6794 | 0.9684 | 0.3047 |
| v7 | validation | 240 | binance | 2847 | 1.0387 | 0.3128 |
| v7 | validation | 240 | gate | 1275 | 1.3943 | 0.3312 |
| v7 | validation | 240 | okx | 1497 | 1.1041 | 0.3020 |
| v8 | development | 30 | binance | 15187 | 1.1296 | 0.3201 |
| v8 | development | 30 | okx | 6785 | 1.1909 | 0.3278 |
| v8 | development | 60 | binance | 7879 | 0.8801 | 0.3094 |
| v8 | development | 60 | gate | 2 | 2.7158 | 0.5000 |
| v8 | development | 60 | okx | 3541 | 0.9173 | 0.3236 |
| v8 | development | 240 | binance | 1825 | 1.0233 | 0.2964 |
| v8 | development | 240 | gate | 1056 | 1.2728 | 0.3201 |
| v8 | development | 240 | okx | 866 | 1.3639 | 0.3418 |
| v8 | validation | 30 | binance | 22472 | 0.8730 | 0.2786 |
| v8 | validation | 30 | gate | 526 | 1.2756 | 0.3289 |
| v8 | validation | 30 | okx | 12831 | 0.8470 | 0.2800 |
| v8 | validation | 60 | binance | 10662 | 0.9737 | 0.2993 |
| v8 | validation | 60 | gate | 639 | 0.9005 | 0.3076 |
| v8 | validation | 60 | okx | 5891 | 0.9909 | 0.3043 |
| v8 | validation | 240 | binance | 2545 | 1.0974 | 0.3096 |
| v8 | validation | 240 | gate | 1129 | 1.3802 | 0.3211 |
| v8 | validation | 240 | okx | 1355 | 1.1036 | 0.2932 |

| arm | period | timeframe_min | sampled | matched | unmatched | match_rate | effect_rows | unmatched_reasons | delta | low | high | p | assets |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| v7 | validation | 30 | 2133 | 2122 | 11 | 0.9948 | 2122 | control_unresolved=11 | -0.0026 | -0.0065 | 0.0014 | 0.2034 | 784 |
| v7 | validation | 60 | 2073 | 2060 | 13 | 0.9937 | 2060 | control_unresolved=13 | 0.0067 | 0.0008 | 0.0125 | 0.0240 | 756 |
| v7 | validation | 240 | 1947 | 1923 | 24 | 0.9877 | 1923 | control_unresolved=24 | -0.0169 | -0.0369 | -0.0025 | 0.0150 | 599 |
| v8 | validation | 30 | 2126 | 2115 | 11 | 0.9948 | 2115 | control_unresolved=11 | -0.0024 | -0.0062 | 0.0013 | 0.2144 | 782 |
| v8 | validation | 60 | 2055 | 2043 | 12 | 0.9942 | 2043 | control_unresolved=12 | 0.0078 | 0.0029 | 0.0128 | 0.0055 | 752 |
| v8 | validation | 240 | 1893 | 1870 | 23 | 0.9878 | 1870 | control_unresolved=23 | -0.0134 | -0.0338 | 0.0002 | 0.0790 | 591 |

| arm | period | timeframe_min | reason | unmatched |
|---|---|---|---|---|
| v7 | validation | 30 | control_unresolved | 11 |
| v7 | validation | 60 | control_unresolved | 13 |
| v7 | validation | 240 | control_unresolved | 24 |
| v8 | validation | 30 | control_unresolved | 11 |
| v8 | validation | 60 | control_unresolved | 12 |
| v8 | validation | 240 | control_unresolved | 23 |

匹配随机对照固定同一数据流方向、时期、事前波动桶、退出引擎和 0.2% 往返成本。`sampled` 是全部抽样目标，`matched` 才是进入效应估计的样本；未匹配项及原因完整保留，不能伪装成 100% 匹配。验证段只有 1H 的 V7/V8 相对随机入场为显著正值；30m 没有优势，4H 的独立 PF 虽大于 1，但相对匹配随机对照仍不能确认策略本身的增量价值。

多空表现明显随年份翻转：验证段空头强于多头，开发段 4H 则是多头强。这支持市场状态门，不能据此把 V8 固化成静态空头版。

## 被过滤与保留样本的特征

| period | timeframe_min | v8_kept | admissions | outcome_rows | executed | realized_10r | mfe_10r | net_positive | mean_rope_distance_atr | mean_efficiency3 | mean_current_volume_ratio |
|---|---|---|---|---|---|---|---|---|---|---|---|
| development | 30 | False | 4198 | 4198.0000 | 2801 | 7 | 29 | 1009 | 3.6410 | 0.6291 | 9.6049 |
| development | 30 | True | 26088 | 26088.0000 | 21736 | 136 | 565 | 7000 | 1.6908 | 0.4550 | 2.9560 |
| development | 60 | False | 2283 | 2283.0000 | 1515 | 4 | 8 | 506 | 3.6150 | 0.6392 | 9.5380 |
| development | 60 | True | 13701 | 13701.0000 | 11298 | 49 | 185 | 3540 | 1.7131 | 0.4480 | 2.8739 |
| development | 240 | False | 597 | 597.0000 | 403 | 2 | 5 | 129 | 3.7682 | 0.6166 | 27.7692 |
| development | 240 | True | 4350 | 4350.0000 | 3707 | 28 | 98 | 1166 | 1.6656 | 0.4480 | 3.1889 |
| validation | 30 | False | 7252 | N/A | N/A | N/A | N/A | N/A | 3.7128 | 0.6359 | 16.1160 |
| validation | 30 | True | 42550 | N/A | N/A | N/A | N/A | N/A | 1.6688 | 0.4303 | 4.2106 |
| validation | 60 | False | 3966 | N/A | N/A | N/A | N/A | N/A | 3.7790 | 0.6444 | 15.9798 |
| validation | 60 | True | 20628 | N/A | N/A | N/A | N/A | N/A | 1.6933 | 0.4235 | 3.6323 |
| validation | 240 | False | 1002 | N/A | N/A | N/A | N/A | N/A | 3.7853 | 0.6063 | 20.7559 |
| validation | 240 | True | 5978 | N/A | N/A | N/A | N/A | N/A | 1.6632 | 0.4230 | 4.4962 |

| period | timeframe_min | failure_reason | filtered_admissions | executed | missed_realized_10r | removed_nonpositive |
|---|---|---|---|---|---|---|
| development | 30 | cost_dominated_tiny_risk | 18 | 18 | 0 | 18 |
| development | 30 | initial_stop_later | 1120 | 1120 | 0 | 1120 |
| development | 30 | initial_stop_within3 | 138 | 138 | 0 | 138 |
| development | 30 | not_executed_occupied | 1397 | 0 | 0 | 0 |
| development | 30 | opposite_signal_loss | 512 | 512 | 0 | 512 |
| development | 30 | positive_below10r | 1002 | 1002 | 0 | 0 |
| development | 30 | realized_ge10r | 7 | 7 | 7 | 0 |
| development | 30 | trailing_giveback_loss | 4 | 4 | 0 | 4 |
| development | 60 | cost_dominated_tiny_risk | 8 | 8 | 0 | 8 |
| development | 60 | initial_stop_later | 732 | 732 | 0 | 732 |
| development | 60 | initial_stop_within3 | 23 | 23 | 0 | 23 |
| development | 60 | not_executed_occupied | 768 | 0 | 0 | 0 |
| development | 60 | opposite_signal_loss | 241 | 241 | 0 | 241 |
| development | 60 | positive_below10r | 502 | 502 | 0 | 0 |
| development | 60 | realized_ge10r | 4 | 4 | 4 | 0 |
| development | 60 | trailing_giveback_loss | 5 | 5 | 0 | 5 |
| development | 240 | initial_stop_later | 197 | 197 | 0 | 197 |
| development | 240 | initial_stop_within3 | 8 | 8 | 0 | 8 |
| development | 240 | not_executed_occupied | 194 | 0 | 0 | 0 |
| development | 240 | opposite_signal_loss | 69 | 69 | 0 | 69 |
| development | 240 | positive_below10r | 127 | 127 | 0 | 0 |
| development | 240 | realized_ge10r | 2 | 2 | 2 | 0 |

## 因果连续特征筛选：只看开发段

| timeframe_min | feature | direction | auc_net_positive | auc_realized_10r | known | scope |
|---|---|---|---|---|---|---|
| 30 | efficiency3 | 1 | 0.5206 | 0.3766 | 24535 | development_only_aggregate_nonblind_artifact |
| 30 | bb_width_step1 | 1 | 0.4934 | 0.4417 | 24535 | development_only_aggregate_nonblind_artifact |
| 30 | bb_width_ratio_p10 | 1 | 0.5003 | 0.4724 | 24535 | development_only_aggregate_nonblind_artifact |
| 30 | current_volume_ratio | 1 | 0.5056 | 0.3501 | 24535 | development_only_aggregate_nonblind_artifact |
| 30 | current_tr_expansion | 1 | 0.5091 | 0.3601 | 24535 | development_only_aggregate_nonblind_artifact |
| 30 | close_risk_fraction | 1 | 0.4874 | 0.2794 | 24535 | development_only_aggregate_nonblind_artifact |
| 30 | cost_share_of_close_r | -1 | 0.4874 | 0.2794 | 24535 | development_only_aggregate_nonblind_artifact |
| 30 | rope_distance_atr | -1 | 0.4737 | 0.6070 | 24535 | development_only_aggregate_nonblind_artifact |
| 30 | md_gap_atr | 1 | 0.5147 | 0.3882 | 24535 | development_only_aggregate_nonblind_artifact |
| 30 | md_step_atr | 1 | 0.5185 | 0.3720 | 24535 | development_only_aggregate_nonblind_artifact |
| 60 | efficiency3 | 1 | 0.5070 | 0.3481 | 12813 | development_only_aggregate_nonblind_artifact |
| 60 | bb_width_step1 | 1 | 0.5080 | 0.4919 | 12813 | development_only_aggregate_nonblind_artifact |
| 60 | bb_width_ratio_p10 | 1 | 0.5256 | 0.4306 | 12813 | development_only_aggregate_nonblind_artifact |
| 60 | current_volume_ratio | 1 | 0.5039 | 0.4089 | 12813 | development_only_aggregate_nonblind_artifact |
| 60 | current_tr_expansion | 1 | 0.5135 | 0.4738 | 12813 | development_only_aggregate_nonblind_artifact |
| 60 | close_risk_fraction | 1 | 0.4723 | 0.3293 | 12813 | development_only_aggregate_nonblind_artifact |
| 60 | cost_share_of_close_r | -1 | 0.4723 | 0.3293 | 12813 | development_only_aggregate_nonblind_artifact |
| 60 | rope_distance_atr | -1 | 0.4726 | 0.5645 | 12813 | development_only_aggregate_nonblind_artifact |
| 60 | md_gap_atr | 1 | 0.5006 | 0.4801 | 12813 | development_only_aggregate_nonblind_artifact |
| 60 | md_step_atr | 1 | 0.5060 | 0.4309 | 12813 | development_only_aggregate_nonblind_artifact |
| 240 | efficiency3 | 1 | 0.5290 | 0.6710 | 4110 | development_only_aggregate_nonblind_artifact |
| 240 | bb_width_step1 | 1 | 0.4854 | 0.5517 | 4110 | development_only_aggregate_nonblind_artifact |
| 240 | bb_width_ratio_p10 | 1 | 0.4966 | 0.4103 | 4110 | development_only_aggregate_nonblind_artifact |
| 240 | current_volume_ratio | 1 | 0.5113 | 0.6210 | 4109 | development_only_aggregate_nonblind_artifact |
| 240 | current_tr_expansion | 1 | 0.5141 | 0.4944 | 4110 | development_only_aggregate_nonblind_artifact |
| 240 | close_risk_fraction | 1 | 0.5122 | 0.3926 | 4110 | development_only_aggregate_nonblind_artifact |
| 240 | cost_share_of_close_r | -1 | 0.5122 | 0.3926 | 4110 | development_only_aggregate_nonblind_artifact |
| 240 | rope_distance_atr | -1 | 0.4977 | 0.5326 | 4110 | development_only_aggregate_nonblind_artifact |
| 240 | md_gap_atr | 1 | 0.5283 | 0.7452 | 4110 | development_only_aggregate_nonblind_artifact |
| 240 | md_step_atr | 1 | 0.5430 | 0.7591 | 4110 | development_only_aggregate_nonblind_artifact |

| timeframe_min | feature | score_decile | trades | net_win_rate | event_pf | mean_net_return | realized_10r |
|---|---|---|---|---|---|---|---|
| 30 | efficiency3 | 10 | 2454 | 0.3077 | 0.8700 | -0.0037 | 4 |
| 30 | bb_width_step1 | 10 | 2454 | 0.3631 | 1.1407 | 0.0045 | 7 |
| 30 | bb_width_ratio_p10 | 10 | 2454 | 0.3329 | 1.1632 | 0.0047 | 11 |
| 30 | current_volume_ratio | 10 | 2454 | 0.3260 | 0.8601 | -0.0044 | 4 |
| 30 | current_tr_expansion | 10 | 2454 | 0.3566 | 1.0137 | 0.0004 | 7 |
| 30 | close_risk_fraction | 10 | 2454 | 0.3195 | 0.8856 | -0.0053 | 2 |
| 30 | cost_share_of_close_r | 10 | 2454 | 0.3195 | 0.8856 | -0.0053 | 2 |
| 30 | rope_distance_atr | 10 | 2454 | 0.2901 | 1.0545 | 0.0011 | 23 |
| 30 | md_gap_atr | 10 | 2454 | 0.3500 | 1.1901 | 0.0045 | 12 |
| 30 | md_step_atr | 10 | 2454 | 0.3529 | 1.0450 | 0.0013 | 6 |
| 60 | efficiency3 | 10 | 1282 | 0.3214 | 0.8563 | -0.0054 | 1 |
| 60 | bb_width_step1 | 10 | 1282 | 0.3222 | 0.7507 | -0.0118 | 3 |
| 60 | bb_width_ratio_p10 | 10 | 1282 | 0.3471 | 0.8663 | -0.0051 | 3 |
| 60 | current_volume_ratio | 10 | 1282 | 0.3261 | 0.7774 | -0.0102 | 1 |
| 60 | current_tr_expansion | 10 | 1282 | 0.3573 | 0.8575 | -0.0063 | 1 |
| 60 | close_risk_fraction | 10 | 1282 | 0.2871 | 0.7006 | -0.0202 | 3 |
| 60 | cost_share_of_close_r | 10 | 1282 | 0.2871 | 0.7006 | -0.0202 | 3 |
| 60 | rope_distance_atr | 10 | 1282 | 0.2902 | 0.9468 | -0.0014 | 6 |
| 60 | md_gap_atr | 10 | 1282 | 0.3222 | 1.0020 | 0.0001 | 3 |
| 60 | md_step_atr | 10 | 1282 | 0.3222 | 0.9118 | -0.0036 | 0 |
| 240 | efficiency3 | 10 | 411 | 0.3163 | 1.1089 | 0.0091 | 2 |
| 240 | bb_width_step1 | 10 | 411 | 0.2287 | 0.7616 | -0.0266 | 6 |
| 240 | bb_width_ratio_p10 | 10 | 411 | 0.2871 | 0.8803 | -0.0102 | 4 |
| 240 | current_volume_ratio | 10 | 411 | 0.2749 | 0.6606 | -0.0345 | 2 |
| 240 | current_tr_expansion | 10 | 411 | 0.2652 | 0.6335 | -0.0371 | 1 |
| 240 | close_risk_fraction | 10 | 411 | 0.2993 | 0.6984 | -0.0382 | 0 |
| 240 | cost_share_of_close_r | 10 | 411 | 0.2993 | 0.6984 | -0.0382 | 0 |
| 240 | rope_distance_atr | 10 | 411 | 0.3747 | 1.8009 | 0.0353 | 5 |
| 240 | md_gap_atr | 10 | 411 | 0.4793 | 2.9921 | 0.0944 | 9 |
| 240 | md_step_atr | 10 | 411 | 0.4793 | 2.7549 | 0.1149 | 13 |

AUC 和最高十分位汇总表只来自开发段，输入均在信号收盘时可知。独立复核仍确认原发现文件曾暴露验证结果列，因此不能把这次时间切分描述成物理隔离或盲验证。单特征 AUC 普遍接近 0.5，说明不存在一个简单阈值可以把 13 万条信号干净分成好坏。

## 全局图与逐笔案例

![validation metrics](../experiments/active/exp-spike-v8-noise-filter-20260913-v1/summary_v1/validation_v7_v8_metrics.png)

![period retention and PF](../experiments/active/exp-spike-v8-noise-filter-20260913-v1/summary_v1/period_retention_pf.png)

![filtered-case categories](../experiments/active/exp-spike-v8-noise-filter-20260913-v1/summary_v1/filtered_case_categories.png)

![retained_realized_10r](../experiments/active/exp-spike-v8-noise-filter-20260913-v1/summary_v1/case_01_retained_realized_10r.png)

![retained_realized_10r](../experiments/active/exp-spike-v8-noise-filter-20260913-v1/summary_v1/case_02_retained_realized_10r.png)

![filtered_loser](../experiments/active/exp-spike-v8-noise-filter-20260913-v1/summary_v1/case_03_filtered_loser.png)

![filtered_loser](../experiments/active/exp-spike-v8-noise-filter-20260913-v1/summary_v1/case_04_filtered_loser.png)

![missed_realized_10r](../experiments/active/exp-spike-v8-noise-filter-20260913-v1/summary_v1/case_05_missed_realized_10r.png)

![missed_realized_10r](../experiments/active/exp-spike-v8-noise-filter-20260913-v1/summary_v1/case_06_missed_realized_10r.png)

K 线案例各取最多两张：保留的已实现 10R、被过滤的亏损、以及被误删的已实现 10R。图中标明信号收盘、次根开盘入场、退出、方向绳索边缘、3ATR 边界和过滤原因。蓝色区域是信号后的复盘未来，只用于解释图；它不是因果特征。由于发现文件的隔离缺陷，本轮仍不能作盲选择声明。

## 还能怎样继续降噪

1. **市场状态门**：单独研究信号收盘前的全市场上涨比例、同时站上六线比例、BTC/ETH 4H 状态、横截面成交额与波动扩散。目标是识别 8·19 这类普涨启动期；必须按月前推验证，不能用当天涨幅榜反选币。

2. **同事件折叠与组合风险门**：前端把同币同方向、跨交易所、相近时间的确认合并成一个市场事件，交易层限制高度相关币同时暴露。这会降低通知和开仓频率，但需与信号质量分开评价。

3. **早期失败退出**：因为初始止损和亏损反向退出解释绝大部分亏损，可单变量测试入场后 2–3 根未继续创新高/新低、重新跌回/涨回六线时提前退出。它改变退出而非入场，不能和 V8 门槛一次打包。

4. **跨交易所先行确认**：测试一个交易所先突破、其他交易所成交量/OI 随后确认的时序结构。资金费率、持仓量和订单簿只能使用当时可获得的快照，并要单独记录覆盖缺失。

5. **前向影子对照**：V7 与 V8 同时只记录、不推送、不下单，积累新的盲样本。当前验证数据已经被研究过，不能再靠反复调 3ATR 得到可信提升。

## 当前裁决

V8 作为独立 TradingView 研究指标成立：它降低追高追空型噪音并保留大多数大趋势。它暂不替换生产 V7/V1 监控，也不改变 Bark。若要继续提高收益，优先验证市场状态门和早期失败退出；继续强化单根量价只会重演误删尾部赢家的问题。

## Holdout 暴露与诚实边界

本冻结 V8 配置记录为 **holdout-era exposure #1**。V7 基础数据此前已经被其他研究看过，且本轮 `discovery_v1` 还物理携带了验证 outcome；所以它不是盲 holdout，也不具备生产晋级证据等级。没有记录显示在验证回放后修改过 3ATR，但数据可见本身已经破坏隔离。只有新的前向影子样本能恢复独立证据。成本沿用 0.2% 往返假设，未完整计入资金费、订单簿冲击和共享组合容量；收盘回撤会低估 K 线内回撤。

## 复现命令

```bash
.venv/bin/python -m pytest -q tests/evaluation/test_spike_v8_replay.py tests/evaluation/test_spike_v8_noise_study.py tests/evaluation/test_spike_v8_report.py
.venv/bin/python -m yoyo.evaluation.spike_v8_report --replay experiments/active/exp-spike-v8-noise-filter-20260913-v1/replay_v1 --discovery experiments/active/exp-spike-v8-noise-filter-20260913-v1/discovery_v1 --output experiments/active/exp-spike-v8-noise-filter-20260913-v1/summary_v1 --report analysis/p1_spike_v8_noise_filter_20260913.md
.venv/bin/python scripts/md_to_html.py analysis/p1_spike_v8_noise_filter_20260913.md --out-dir analysis/html
```

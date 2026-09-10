# SPIKE V3 降噪总览：原版与六次固定探索

**A—F没有任何一项同时通过四个固定门。** 原V3为了提前发现启动，放宽了原V1的蓄势、密集及单根量价硬门；因此观察数量增多。减少提示必须同时检查错过多少正确启动，不能只看信号更少或个别漂亮截图。

最新F：公开标签减少38.66%，保留原及时命中1171/1463（80.04%），大幅命中643/947（67.90%）；对应历史独立事件平均净收益33.82bp，匹配超额-114.95bp。未部署，也未证明原生Pine逐根一致。

## 一眼看取舍

![原V3和A—F降噪召回取舍](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v3-price-acceptance-20260910-v1/results/figures/noise_recall_tradeoff.png)

绿色区域是各图两项结构目标同时满足的位置；还须另一幅图的门、标签≤6021及正匹配超额/Holm<.01。横轴是公开标签减少，不能直接叫假信号减少。原V1缺少认证的原V3命中保留率，故只列表、不猜散点。

## 原版基准

| 版本 | 原信号/父 | 合并标签 | 1660正例及时命中 | 及时召回 | 947大幅召回 | 自然退出净胜率 | 平均净bp | 各自匹配超额bp | 对照来源 |
|---|---|---|---|---|---|---|---|---|---|
| 原版V1 | 86 | 86 | 14 | 0.84% | 1.16% | 47.44% | 643.95 | 404.20 | 原V1认证历史控制，不重新匹配 |
| V3 | 10386 | 12042 | 1463 | 88.13% | 84.27% | 27.59% | 25.01 | -100.24 | 本轮V3/F共同控制 |

## 六项机制，没有把失败隐藏

| 假设 | 唯一新机制 | 公开父 | 确认升级 | 合并标签 | 标签减少 | 保留/1463 | 保留率 | 大幅命中/947 | 大幅召回 |
|---|---|---|---|---|---|---|---|---|---|
| A | 参考仍有效时不再接新父 | 4120 | 1212 | 4683 | 61.11% | 663 | 45.32% | 334 | 35.27% |
| B | 重新完整近零蓄势后突破 | 519 | 333 | 591 | 95.09% | 21 | 1.44% | 19 | 2.01% |
| C | 得到确认后才取得抑制权 | 6777 | 2308 | 7818 | 35.08% | 970 | 66.30% | 508 | 53.64% |
| D | 启动前六均线逐步收拢 | 6209 | 2139 | 7132 | 40.77% | 761 | 52.02% | 426 | 44.98% |
| E | 已收盘4H主线不弱于信号线 | 6458 | 2615 | 7559 | 37.23% | 816 | 55.78% | 493 | 52.06% |
| F | 突破后下一根收盘仍站上边界 | 6940 | 3171 | 7386 | 38.66% | 1171 | 80.04% | 643 | 67.90% |

| 假设 | 少提示≥50%/≤6021 | 旧命中≥90% | 大幅召回≥80% | 正超额且Holm6<.01 | 四门同时 |
|---|---|---|---|---|---|
| A | 通过 | 未通过 | 未通过 | 未通过 | 未通过 |
| B | 通过 | 未通过 | 未通过 | 未通过 | 未通过 |
| C | 未通过 | 未通过 | 未通过 | 未通过 | 未通过 |
| D | 未通过 | 未通过 | 未通过 | 未通过 | 未通过 |
| E | 未通过 | 未通过 | 未通过 | 未通过 | 未通过 |
| F | 未通过 | 未通过 | 未通过 | 未通过 | 未通过 |

F主门保守加回0个未知所属原标签，保守标签数7386；未知不能帮助过门。F时移、拒绝、未知和早子确认合并逐条分解在其报告，旧参考覆盖不能补进1463及时保留的分子。

## 收益必须带各自随机对照

| 假设 | 自然退出净胜率 | 自然退出 | 截尾 | 净均值bp | 匹配事件bp | 匹配随机bp | 配对超额bp | 冻结原p | Holm6 | 各自对照日程 |
|---|---|---|---|---|---|---|---|---|---|---|
| A | 27.38% | 3948 | 172 | 29.19 | 28.17 | 109.90 | -81.73 | 0.93421 | 1.00000 | A/B共同日程 |
| B | 26.56% | 482 | 37 | 38.70 | 22.17 | 207.81 | -185.64 | 0.99910 | 1.00000 | A/B共同日程 |
| C | 26.58% | 6483 | 294 | 13.79 | 13.51 | 108.17 | -94.65 | 1.00000 | 1.00000 | C与其当轮V3共同日程 |
| D | 25.19% | 5884 | 325 | 0.02 | -0.88 | 108.71 | -109.59 | 1.00000 | 1.00000 | D与其当轮V3共同日程 |
| E | 29.22% | 6116 | 342 | 36.34 | 34.65 | 158.44 | -123.79 | 1.00000 | 1.00000 | E与其当轮V3共同日程 |
| F | 28.30% | 6572 | 368 | 33.82 | 29.03 | 143.98 | -114.95 | 1.00000 | 1.00000 | F与其当轮V3共同日程 |

**A/B、C、D、E、F使用各自实验冻结的匹配日程，超额不能当作完全相同随机基线下的策略排名。** 每项内部仍是同币×UTC周×因果ATR桶、相同风险退出/20bp的随机入场对照。A—E原p直接读取冻结表，统一展示F已经冻结的Holm6，没有重评分旧策略。原V1控制也是其历史版本。

## 下一步如何使用这些结论

现有证据支持先在呈现上把结构观察、质量升级和参考趋势生命周期明确区分：保留真实发现时点，不让每个观察都长得像新开仓。它能减少重复开仓暗示，但属于交互语义改进，不代表统计上的假信号已经减少。

任何过滤版尚未同时过门时，保持研究状态，不用‘精选/超级趋势’名称冒充成功。下一项研究必须另立机制与事前计划；不继续在同池穷举阈值。即使历史门过，也要未见数据前向验证、真实延迟/成交假设及Pine逐根验证。

## 三张固定F复盘图

下图来自已认证F报告，HYPE/NEAR/PEPE日期固定，每张完整96根；是机制说明，不是收益筛选后的成功案例。

### HYPE

![HYPE固定F复盘](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v3-price-acceptance-20260910-v1/results/figures/hype.png)

### NEAR

![NEAR固定F复盘](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v3-price-acceptance-20260910-v1/results/figures/near.png)

### PEPE

![PEPE固定F复盘](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v3-price-acceptance-20260910-v1/results/figures/pepe.png)

## 风险与诚实声明

同一278个历史OKX合约、1H多头、UTC2026-07-10至09-09共61天，不含BTC/ETH。固定8046锚点：1660正例/6240负例/146未知；947大幅正例。原V3及时命中1463、合并标签12042保持，不以旧持仓覆盖、晚确认或移动时钟修补。原V1不是与V3数量相等的候选池。

这里的正例有固定定义：独立价格突破锚点后24根收盘先达到+4ATR而非-2ATR；大幅子组还要求这24根最高价涨幅至少8%。它们不是人工确认的均线密集启动金标，也不等于按实际入场与退出规则获利的交易。90%保留是本轮检验门，不能解释成未来正确信号不丢的保证。

13项单门加A—F六项顺序探索反复使用已见历史；这是授权研究、每配置第1次消费该配置holdout，不是盲OOS。Holm6没有消除全部研究者选择偏差，也不能证明未来山寨季会有收益。

没有训练新模型，val AUC/训练val样本数不适用；各原报告保留量比/TR描述AUC和top10%毛净收益单特征对照。描述性排序不能替代配对超额。

独立事件可能重叠，未计算账户NAV/最大回撤，不填0；20bp不覆盖全部资金费、滑点和冲击。峰值R与在场覆盖不等于已实现收益。不存在已验证的F原生Pine移植，图表只是存储事件的科学绘图。

## 原报告与可核验证据

[原V1/V3逐门比较](/Users/zhangzc/fable-trading/analysis/html/p1_spike_v3_gate_diagnostic_20260910.html)、[A/B](/Users/zhangzc/fable-trading/analysis/html/p1_spike_v3_focus_20260910.html)、[C](/Users/zhangzc/fable-trading/analysis/html/p1_spike_v3_confirmation_gate_20260910.html)、[D](/Users/zhangzc/fable-trading/analysis/html/p1_spike_v3_formation_gate_20260910_r2.html)、[E](/Users/zhangzc/fable-trading/analysis/html/p1_spike_v3_htf_gate_20260910.html)、[F](/Users/zhangzc/fable-trading/analysis/html/p1_spike_v3_price_acceptance_20260910.html)。

本轮关键文件：[F候选裁决](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v3-price-acceptance-20260910-v1/results/candidate_registry.csv.gz)、[F实际公开事件](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v3-price-acceptance-20260910-v1/results/signals.csv.gz)、[F时钟分解](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v3-price-acceptance-20260910-v1/results/clock_summary.csv)、[F召回](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v3-price-acceptance-20260910-v1/results/retention_summary.csv)、[F收益](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v3-price-acceptance-20260910-v1/results/trade_summary.csv)、[F六假设校正](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v3-price-acceptance-20260910-v1/results/structural_holm_family.csv)。

独立QA：[preflight_review.json](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v3-price-acceptance-20260910-v1/qa/preflight_review.json)、[independent_review.json](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v3-price-acceptance-20260910-v1/qa/independent_review.json)。

### 复现与完整性

本builder先提交；先完成并认证F prepare/evaluate及F图文报告，再用其完整validation SHA运行。只读取已有结果，不重新检测或评分，拒绝覆盖已完成总览。

```bash
.venv/bin/python -m yoyo.evaluation.spike_burst_v3_noise_overview --validation-sha a0cb43b3b7d548148938b9404d1744d85b5d1a60ccb8b35ab04edff012b5a570
```

F prepared SHA：32abdf5316732247e4912cd17e3c7c94353ba6756a33df38c007fd7933d83a42；F validation SHA：a0cb43b3b7d548148938b9404d1744d85b5d1a60ccb8b35ab04edff012b5a570。输入/输出/散点图/固定案例图SHA记录在/Users/zhangzc/fable-trading/experiments/active/exp-spike-v3-price-acceptance-20260910-v1/results/noise_overview_manifest.json。

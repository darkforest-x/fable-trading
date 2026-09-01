# 15m L2 28 特征分组消融（2026-09-02）

## 结论先行

本轮最终裁决：**REJECT**。LONG 在 tune 期选中 **ma_plus_trend_volume_volatility**，SHORT 选中 **ma_plus_trend**。28 个特征不是先验真理，而是旧基线；当前 YOLO 候选上是否应该保留，必须由时间外经济结果决定。

![特征组消融诊断](output/ma_launch_l2_feature_group_ablation_v1/feature_group_ablation_diagnostics.png)

## 数据统计

| split | LONG 独立事件 | SHORT 独立事件 | 合计 |
|---|---:|---:|---:|
| train | 190 | 227 | 417 |
| tune | 118 | 111 | 229 |
| final_validation | 157 | 85 | 242 |

数据范围：2026-01-01 至 2026-05-03；holdout 读取：0。每项指标只用 dependency representative，避免同一行情的重叠检测框重复计票。

## tune 期预注册选择

| 方向 | 方案 | 特征数 | iter | 精确 top-10% 净收益 | q90 n | q90 净收益 | Spearman | 健康 | 入选 |
|---|---|---:|---:|---:|---:|---:|---:|---|---|
| LONG | ma_spread_only | 1 | 1 | -1.239% | 40 | -1.239% | -0.3577 | False | False |
| LONG | ma_structure | 11 | 16 | -0.795% | 12 | -0.796% | 0.2190 | True | False |
| LONG | context_only | 17 | 1 | -1.187% | 46 | -1.187% | -0.3592 | False | False |
| LONG | ma_plus_trend | 16 | 15 | -0.635% | 12 | -0.639% | 0.1609 | True | False |
| LONG | ma_plus_trend_volume | 19 | 15 | -0.431% | 12 | -0.438% | 0.1737 | True | False |
| LONG | ma_plus_trend_volume_volatility | 24 | 15 | -0.222% | 12 | -0.163% | 0.1518 | True | True |
| LONG | full_28 | 28 | 17 | -0.930% | 13 | -0.931% | 0.0649 | True | False |
| SHORT | ma_spread_only | 1 | 16 | -0.376% | 16 | -0.376% | 0.3242 | True | False |
| SHORT | ma_structure | 11 | 9 | -0.422% | 12 | -0.478% | 0.2877 | True | False |
| SHORT | context_only | 17 | 17 | -0.338% | 12 | -0.409% | 0.3647 | True | False |
| SHORT | ma_plus_trend | 16 | 29 | -0.137% | 12 | -0.209% | 0.2557 | True | True |
| SHORT | ma_plus_trend_volume | 19 | 10 | -0.367% | 12 | -0.418% | 0.2082 | True | False |
| SHORT | ma_plus_trend_volume_volatility | 24 | 18 | -0.629% | 12 | -0.685% | 0.3173 | True | False |
| SHORT | full_28 | 28 | 17 | -0.650% | 12 | -0.681% | 0.2969 | True | False |

选择只看 3 月 tune；4 月 final 没有参与选方案。LightGBM early stopping 也使用同一个 tune，因此仍有“既早停又选组”的乐观偏差，不能把 tune 最优当成生产结论。

## April final 同表对照

| 配置 | LONG/SHORT 特征 | top-10% 净收益 | q90 n | q90 净收益 | 胜率 | 置换 p | 事件减匹配对照 | 8/8 对照均跑赢 |
|---|---|---:|---:|---:|---:|---:|---:|---|
| 单特征基线 | ma_spread_only / ma_spread_only | +0.578% | 48 | +0.578% | 35.42% | 0.075392 | +0.744% | False |
| 旧 28 特征 | full_28 / full_28 | +0.898% | 20 | +1.231% | 40.00% | 0.072093 | +1.134% | True |
| tune 入选组合 | ma_plus_trend_volume_volatility / ma_plus_trend | -0.690% | 18 | -0.763% | 22.22% | 0.972603 | -0.529% | False |

AUC 只作诊断：入选组合 AUC=0.4886，PR-AUC=0.2960，Spearman=-0.0231。裁决仍以扣成本收益、置换检验、样本量和匹配对照为准。

## LONG / SHORT final

| 方向 | 入选方案 | final n | q90 n | q90 净收益 | 胜率 | p |
|---|---|---:|---:|---:|---:|---:|
| LONG | ma_plus_trend_volume_volatility | 157 | 10 | -1.226% | 20.00% | 0.109989 |
| SHORT | ma_plus_trend | 85 | 8 | -0.184% | 25.00% | 0.764824 |

## 匹配随机对照与裁决门

匹配条件保持同币、同月、同 UTC 8 小时时段、同 ATR 桶、同方向、同 TP/SL/horizon/cost。完整 assignment：8/8；入选事件完整覆盖：18/18；全部 assignment 跑赢：False；平均事件减对照：-0.529%。

| 预注册门 | 通过 |
|---|---|
| aggregate_beats_matched_controls_every_assignment | False |
| aggregate_minimum_30_selected_dependency_blocks | False |
| aggregate_outcome_permutation_p_lt_0_01 | False |
| aggregate_selected_q90_net_strictly_better_than_full_28 | False |
| aggregate_selected_top_decile_net_positive | False |
| baseline_reproduction_required | True |
| each_side_minimum_10_selected_dependency_blocks | False |
| neither_side_selected_q90_net_negative | False |
| 全部门 | **False** |

## 为什么是这些 28 个，以及本轮回答了什么

28 个是 2026-07-07 为当时 strict-rule 候选人工设计的旧基线：11 个均线密集/持续性、5 个价格与趋势、3 个成交量、5 个波动率、4 个动量。它们覆盖了合理的市场维度，但不是自动从当前 YOLO 候选上筛出来的，也不保证全部有用。

本轮把“特征是否有用”改成可证伪问题：MA 单特征、MA 结构、纯上下文，以及逐组加入趋势/量能/波动/动量，固定模型与时间切分逐一比较。若 tune 入选组合在 final 失败，说明当前 229 个 tune 事件不足以稳定选择，不能继续凭感觉删特征。

## 基线复现与无前视

旧 28 特征 LONG/SHORT 的全部 final 分数、阈值和 KEEP 决策复现通过：True；最大分数差 9.975e-17。特征来自已冻结的 L1 决策 bar 及以前，标签才看未来。

## 风险与诚实声明

- 训练 417、tune 229、final 242 个独立事件，按方向再拆后仍偏小；特征选择方差可能很大。
- 七个方案在同一 tune 上比较，存在多重比较；未用 final 反向重选。
- 当前 YOLO 是 completed-history 检测器，消费过 core 后 2–9 根 K；本实验不能冒充 tip 实盘信号。
- 未读取 2026-05-04 后 holdout，未 promote、部署、改 ACTIVE/frozen/forward、发 Telegram 或下单。

## 复现命令

    PYTHONPATH=. .venv/bin/python -m scripts.research_15m_ma_launch_l2_feature_group_ablation --select
    PYTHONPATH=. .venv/bin/python -m scripts.research_15m_ma_launch_l2_feature_group_ablation --evaluate-final
    PYTHONPATH=. .venv/bin/python -m scripts.research_15m_ma_launch_l2_feature_group_ablation --render --verify --report
    python3 scripts/md_to_html.py analysis/p3_15m_ma_launch_l2_feature_group_ablation_20260902.md --out-dir analysis/html

## 下一步

只有全部预注册门通过，才值得在新的、未见时间段复验。本报告不授权读取 holdout、promote 或部署。

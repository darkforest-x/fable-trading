# 15m 均线密集启动：L2 全局上下文判断层 v1

## 结论

本轮结论是：**未通过研究门**。L1 继续只负责在 18/19 根局部图里提出候选；L2 在 L1 最后一根可见 K 线收盘后，读取最多 168 根历史形成的 28 个因果特征，再用调参段固定的 q90 分数门过滤。最终验证段没有参与训练、早停或阈值选择。

这不是生产启用结论。当前 L1 是完成形态检测器，已经看过核心后的 2–9 根确认 K；而且 L1 的 `best.pt` 由 2025-12 至 2026-05 的 chronological val 选择。虽然本轮剔除了所有与 L1 train/val 图片相交的候选窗口，系统级结果仍应解释为“冻结 L1 条件下的 L2 时间外排序”，不是完整滚动训练仿真。真正生产确认仍需新的前向新鲜样本。

![L2 保留与淘汰全局图总览](output/ma_launch_l2_global_context_v1/l2_kept_vs_rejected_overview.png)

高清单图浏览：[100 张 L2 KEEP / REJECT 全局图](p3_15m_ma_launch_l2_global_context_gallery_20260901.html)。

## 五模型候选血缘与本轮输入

本轮不是把五个 checkpoint 的框直接混在一起。上游冻结对照是 `exp-15m-ma-launch-model-compare-all3d-20260831-v1`；其预注册、汇总和模型表均以 SHA-256 固定。本轮选择其中 `grade_a8k_neg24k_full40_1280` 作为唯一 L1 输入，因为它对应当前 Owner Grade-A 正负样本几何与原生 1280 合同。

另外四个模型的弱/强标签、原生分辨率、窗口、核心和确认长度、confidence 标定均不同；混池后同一个 L2 threshold 没有统一语义，也会同时改变多个变量。因此五模型结果只提供 checkpoint 血缘与视觉对照，**没有把近三日候选数量、confidence 或 holdout 表现用于选模/调参，其他四臂也没有作为 L2 特征**。

## 数据与时间纪律

| 项目 | 数值 |
|---|---:|
| 冻结币种 | 54 |
| 冻结 15m K 线 | 679,104 |
| 冻结范围 | 2025-12-24T00:00:00+00:00 → 2026-05-04T00:00:00+00:00 |
| L1 原始结构合法框 | 25,911 |
| 跨午夜重叠 episode | 3,808 |
| L2 可用 episode | 3,779 |
| 完整暴露依赖块 | 974（最大块 22 个 episode） |
| train / tune / final val episode | 1,779 / 821 / 1,021 |
| train / tune / final val 独立块 | 417 / 229 / 242 |
| matched-control 行 | 1,824（8 / 8 个分配可用） |
| LightGBM / NumPy / pandas | 4.6.0 / 2.0.2 / 2.3.3 |
| 确定性训练 | CPU · deterministic=true · force_col_wise=true · num_threads=1 |
| 冻结快照 commit | `a735c14788671da6784ada1c8eaa6cad139d04bd` |
| 远端 L1 扫描 commit | `a735c14788671da6784ada1c8eaa6cad139d04bd` |
| L2 数据集 commit | `058977365adf8e2a7c668cedd1c9797c019382df` |
| L2 训练评估 commit | `058977365adf8e2a7c668cedd1c9797c019382df` |
| holdout 读取 | 0 |

信号时钟固定为：`window_end_time` 是 L1 最后一根可见 K 的开盘时间；`available_at = window_end_time + 15min`；L2 特征只到该收盘；TP5/SL2/72 标签从 `available_at` 对应的下一根开盘开始。每个事件的完整暴露区间是 `[available_at-42h, available_at+18h)`，train→tune 与 tune→final val 各留 60 小时 purge。直接或传递重叠的同币区间属于同一依赖块；只有每块最早事件进入训练、早停、阈值选择和最终指标，后续事件只用于评分与全局图复盘。

本轮是显式多阶段血缘，不把不同提交伪装成同一个二进制：快照和远端 L1 扫描固定在上表对应 commit；完整暴露隔离、确定性训练、收据守门与报告修复随后落在数据集/训练 commit。远端回执逐项固定 L1 权重、训练 manifest、renderer、L2 feature/label builder 及候选账本 SHA-256；本地阶段重新校验这些哈希后才读取候选。扫描之后的改动不重算、筛选或调节 L1 候选。

这项 60 小时/依赖块规则是在扫描期间的代码审计中、**任何 L2 outcome、score 或收益结果生成之前**写入预注册 integrity amendment；它修复原 18 小时 label-only purge 的证据隔离缺口，没有改变 L1 权重/阈值、TP/SL、期限或成本。

LightGBM 4.6.0 的官方参数说明要求 `deterministic=true` 时同时固定 `force_col_wise` 或 `force_row_wise`，以避免潜在数值不稳定。本轮固定 CPU、`force_col_wise=true`、单线程及全部抽样 seed，并把实际 Python/平台/包版本写入训练回执。来源：https://lightgbm.readthedocs.io/en/v4.6.0/Parameters.html#deterministic

## 最终验证结果

| 口径 | n | 毛收益 | 扣 0.2% 净收益 | 胜率 | AUC | Spearman |
|---|---:|---:|---:|---:|---:|---:|
| L1 独立块首个候选 | 242 | +27.4 bp | +7.4 bp | 29.34% | 0.5646 | 0.1652 |
| L2 final top-decile | 24 | +5.0 bp | -15.0 bp | 25.00% | — | — |
| L2 冻结 tune-q90 门 | 41 | +23.4 bp | +3.4 bp | 29.27% | — | — |
| 单特征 ma_spread baseline top-decile | 24 | +6.4 bp | -13.6 bp | 29.17% | 0.5194 | 0.0563 |
| 匹配随机对照（L2 选中对应） | ≥39 / 分配 | — | -10.2 bp | — | — | — |
| L2 减匹配对照 | — | — | +18.4 bp | — | — | — |

Outcome permutation（固定分数、打乱收益 10,000 次）单尾 `p=0.656434`。AUC 只作诊断，不进入成功裁决。
匹配对照若缺少任何一个预注册分配（本轮缺失：`[]`），会直接判门失败，不把“没有配到样本”当成胜出。

## 预注册门

| 门 | 结果 |
|---|---:|
| beats_matched_controls_every_assignment | FAIL |
| frozen_threshold_net_positive | PASS |
| minimum_30_selected_dependency_blocks | PASS |
| outcome_permutation_p_lt_0_01 | FAIL |
| top_decile_net_positive | FAIL |

总判定：**FAIL**。

## 特征贡献（gain 前 10）

| 排名 | 特征 | gain |
|---:|---|---:|
| 1 | atr_pct | 0.0139393 |
| 2 | slow_slope_12 | 0.00686847 |
| 3 | spread_chg24 | 0.00635734 |
| 4 | volume_ratio | 0.00580524 |
| 5 | dense_frac48 | 0.00316861 |
| 6 | ret_48 | 0.00281403 |
| 7 | atr_pct_ratio96 | 0.00263989 |
| 8 | vol_ratio_mean8 | 0.000697045 |
| 9 | ret_24 | 0 |
| 10 | ret_12 | 0 |

## 如何理解数字变化

- 如果 L2 通过，说明“局部框像”与“全局值得做”确实可以分层：L1 保持召回，L2 用历史波动、均线间距/收敛、位置、趋势、量能和近端动量筛掉一部分全局不协调候选。结论只按完整暴露依赖块的首个因果事件计数，不把重叠行情重复算成证据。
- 如果 L2 未通过，不能靠调低阈值把结果救回来；本配置应记录为负结果。下一轮只能预注册一个新变量，例如固定 epoch 的 L1 checkpoint、不同 L2 表征或真正的新鲜前向数据。
- 匹配随机对照与 L1 候选使用相同币、月份、UTC 8 小时时段、因果 ATR 五分位、方向、障碍、期限与成本；每个控制点离任何 L1 episode 至少 72 根 K。

## 风险与诚实声明

- 54 币来自当前存在的深历史文件，存在生存者偏差；所有实验臂和随机对照使用同一 cohort，但这不能消除绝对收益偏差。
- L1 权重拟合数据止于 2025-11-29，候选期从 2026-01-01 开始；不过 `best.pt` 的 epoch 选择看过延续到 2026-05-03 的 L1 chronological val。精确重叠图已隔离，checkpoint-selection hindsight 仍存在。
- L1 是 completed-shape 研究检测器，不是 tip/tip-1/tip-2 信号。L2 的 `available_at` 是完整检测窗右端，而不是红框核心结束时间。
- 本轮没有读取 ≥2026-05-04 holdout，没有调 L1 confidence/NMS/window，没有改 TP/SL/horizon/成本，没有 promote、部署、写 forward、发 Telegram 或下单。
- 静态 pre-holdout PASS 最多允许进入新的前向验证；不能替代 100 笔新鲜前向终审。

## 复现命令

```bash
git checkout 058977365adf8e2a7c668cedd1c9797c019382df
PYTHONPATH=. .venv/bin/python scripts/research_15m_ma_launch_l2_global_context.py --freeze-snapshot
bash scripts/run_15m_ma_launch_l2_global_context_on_3060.sh --check --batch-size 32
bash scripts/run_15m_ma_launch_l2_global_context_on_3060.sh --stage --batch-size 32
bash scripts/run_15m_ma_launch_l2_global_context_on_3060.sh --start --batch-size 32
bash scripts/run_15m_ma_launch_l2_global_context_on_3060.sh --status
# 仅在远端 scan.exit=0 且原子终态回执存在后收集候选账本：
bash scripts/run_15m_ma_launch_l2_global_context_on_3060.sh --collect
PYTHONPATH=. .venv/bin/python scripts/research_15m_ma_launch_l2_global_context.py --build-dataset
PYTHONPATH=. .venv/bin/python scripts/research_15m_ma_launch_l2_global_context.py --train-evaluate
PYTHONPATH=. .venv/bin/python scripts/research_15m_ma_launch_l2_global_context.py --render
PYTHONPATH=. .venv/bin/python scripts/research_15m_ma_launch_l2_global_context.py --verify
PYTHONPATH=. .venv/bin/python scripts/build_15m_ma_launch_l2_global_context_report.py
python3 scripts/md_to_html.py analysis/p3_15m_ma_launch_l2_global_context_20260901.md --out-dir analysis/html
```

## 下一步选项

1. 若 FAIL：停止本配置，不在 final val 上继续调阈值；另开单变量预注册。
2. 若 PASS：先跑只读前向观察，不自动 promote/deploy；何时消耗 holdout 或接入 tip 路径仍需 Owner 单独批准。
3. 若 Owner 更在意“肉眼全局形态”而非收益排序，可另建全局图分类器；它和本轮 LightGBM 经济判断是不同目标，不能混成一轮。

QA：100 张高清图逐图重渲染，失败 0；overview SHA-256 `dcfc4deb530b1d37f46183370fc69a53c2b804e377c014a51a5107c41ae8ab00`；gallery SHA-256 `4feb5e705cb174c17feefa3ab1345408516756911cb65d03b8be1dcb60858c4b`。

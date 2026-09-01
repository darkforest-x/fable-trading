# 15m L2 历史参考事件扩充实验（2026-09-02）

## 结论先行

本轮裁决：**REJECT_REFERENCE_AUGMENTATION**。这次真正把旧 10,000 正图与 10,000 匹配负图的事件血缘接回 K 线；原形态正负标签没有冒充盈亏，而是逐事件重新计算固定 TP5/SL2/72 收益。参考事件只加入训练，真实 L1 的 tune 与 final-validation 完全不变。

## 数据统计

| 项目 | 数量 |
|---|---:|
| 原始参考 manifest | 20,000 |
| 训练截止前可用参考窗口 | 18,364 |
| 成功生成经济标签 | 18,069 |
| 参考数据源 | 359 个 CSV |
| 参考事件时间 | 2021-09-02 至 2026-02-26 |
| 原真实 L1 独立训练块 | 417 |
| 扩充后独立训练块 | 13,867 |
| 其中参考事件 | 13,476（97.18%） |
| 因跨来源依赖桥合并的原 L1 代表 | 26 |
| 固定 tune 独立事件 | 229 |
| 固定 final 独立事件 | 242 |
| final 时间 | 2026-04-01 至 2026-05-03 |
| holdout 读取 | 0 |

图片数不是独立事件数：最新 Grade-A 8,000 图只有 1,043 个事件的 7–8 个位置变体；本轮使用的是旧 10,000 个正事件和 10,000 个匹配负事件的唯一血缘，并再次按完整输入＋标签暴露合并依赖块。

## 上万图片与 Gold 到底用在哪里

YOLO 图片回答的是“这里有没有目标形态”，L2 回答的是“冻结 L1 真正报出这个候选后，按下一根开盘进入，未来 72 根能否先碰 5ATR 止盈而不是 2ATR 止损”。两者的目标不同，所以图片不能直接按张数变成 L2 盈亏样本。

仓库另有 1,345 条 short-only 训练正例资产，但完整 1,345 张尚未逐样本重新确认，70 个独立⭐框才是最高质量子集；LONG 对应的逐样本 Owner Gold 仍不完整。本轮没有把不同方向、不同几何语义或未确认镜像静默拼成收益标签。

这次不是没有使用旧图，而是把每张图重新联结 K 线并计算收益。结果显示形态正负与经济 TP 并不等价：

| 方向 | 形态正图 TP率 | 形态负图 TP率 | 差值 |
|---|---:|---:|---:|
| LONG | 25.32% | 22.90% | +2.42% |
| SHORT | 25.20% | 26.66% | -1.46% |

SHORT 甚至是形态负图的 TP 率更高。这不否定这些图片对 YOLO 的价值，只说明它们不能代替真实 L1 候选上的 L2 收益监督。

![参考扩充数据与结果诊断](output/ma_launch_l2_reference_augmentation_v1/reference_augmentation_diagnostics.png)

## 与原模型同表对照

| 配置 | top-decile 净均值 | q90 n | q90 净均值 | 胜率 | 置换 p |
|---|---:|---:|---:|---:|---:|
| 原 side-split（精确复现） | +0.939% | 20 | +1.231% | 40.00% | 0.072093 |
| 参考事件扩充 | -0.165% | 116 | +0.129% | 28.45% | 0.668933 |
| 扩充单特征 ma_spread | +0.907% | 89 | +0.469% | 30.34% | 0.072293 |

全特征参考扩充 AUC=0.4864，PR-AUC=0.2869，Spearman=0.0873。AUC 仅作诊断，裁决仍看扣成本收益、置换检验与匹配随机对照。

扩充全特征模型的 LONG/SHORT 最佳迭代分别只有 3 / 1；固定 q90 门在 final 上分别放过 22.29% / 95.29%。尤其 SHORT 从理论上的 tune 约 10% 漂到 final 的 95.29%，是明显的目标域校准失效，不是“轮数没跑够”。

## LONG / SHORT

| 方向 | final n | q90 n | q90 净均值 | 胜率 | p |
|---|---:|---:|---:|---:|---:|
| LONG | 157 | 35 | -0.184% | 25.71% | 0.335066 |
| SHORT | 85 | 81 | +0.265% | 29.63% | 0.722928 |

## 匹配随机对照

要求同币、同月、同 UTC 8 小时时段、同 ATR 桶、同方向、同 TP/SL/horizon/cost。完整 assignment 为 8/8；所有 assignment 均跑赢=False；完整覆盖入选事件=109/116；平均事件减对照=+0.345%。

## 基线与无前视验证

原 LONG/SHORT 模型、分数、百分位、q90 阈值和 KEEP 决策精确复现：`True`。参考窗口的特征只读 `window_end_i` 及以前；标签从下一根开盘开始。每个源文件只物理读取到所需 72 根 outcome 的前缀，最大读取时间早于 holdout；`holdout_rows_opened=0`。

## 解读

本实验只回答：把历史参考窗口按真实收益重新标注后加入 L2 训练，能否改善真实 L1 候选的时间外排序。它不把形态负图当亏损，也不拿参考图片自身做最终验收。若结果失败，含义是这些自动参考事件与真实 L1 提案分布不一致或经济信息不足，不能继续靠堆图片数量解决。

正确的数据路径应是：继续让 Owner Gold、正图和 hard negative 服务 L1 形态检测；L2 则用同一个冻结 L1 在更长历史、更多币种上真实扫描出的候选逐事件打 TP/SL/timeout 标签，并保留同币时间依赖、时间切分和 LONG/SHORT 分训。这样扩大的才是 L2 的目标域样本，而不是另一个分布的漂亮形态图。

## 实际 final 输入图核查

下图是 24 张模型实际评分的 final-validation 输入：固定包含 KEEP 中的赢家、KEEP 中的非 TP、DROP 中的赢家，再用未重复高分事件补齐；无人工删图。每张仅显示决策时已闭合的 168 根 K 线，图外 outcome 文本只用于事后审计。

![24 张实际 final 输入总览](output/ma_launch_l2_reference_augmentation_v1/diagnostic_chart_overview.png)

高清逐图浏览：[24 张实际 final 输入](p3_15m_ma_launch_l2_reference_augmentation_diagnostic_gallery_20260902.html)。

## 风险与诚实声明

- 10,000 正例来自 Owner 接受的自动参考族，不是 10,000 个逐张手工 Gold；来源字段保留但未作为模型特征。
- 参考正例的筛选曾使用 completed-history 形态证据，因此可能存在样本选择偏差；最终指标只在未改动的真实 L1 候选上计算。
- 当前 L1 仍是使用 post-core 2–9 根的 completed-history 检测器，不得冒充 tip 实盘信号。
- 未读取 holdout，未 promote、部署、改 ACTIVE/frozen/forward、发 Telegram 或下单。

## 复现命令

```bash
PYTHONPATH=. .venv/bin/python -m scripts.research_15m_ma_launch_l2_reference_augmentation --all
PYTHONPATH=. .venv/bin/python -m scripts.render_15m_ma_launch_l2_reference_augmentation_diagnostics
python3 scripts/md_to_html.py analysis/p3_15m_ma_launch_l2_reference_augmentation_20260902.md --out-dir analysis/html
```

## 下一步

只有全部预注册经济门通过，才值得申请新的未见时间段复验；本报告本身不授权 holdout、promote 或部署。

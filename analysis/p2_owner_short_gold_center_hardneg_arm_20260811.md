# P2 Owner-short compact YOLO Hard-Negative第二训练臂（2026-08-11）

## 结论先行

- 已按交接规范§6完成第二训练臂的数据构建：train为`1143 positive + 1143 easy negative + 2286 hard negative`，总负正比 **3:1**，hard占训练负样本 **66.67%**。
- 2286个hard negative不是3倍随机空背景：**916**个来自Owner明确判为`long`、且不碰任何short保护区的相似平台；**1370**个来自原train时间块6858个安全背景的baseline模型排序。
- W12–19的hard-negative数量逐档严格等于train正例的2倍；完整val仍为202正/200 easy负，未加入hard negative，也未参与选择。
- 旧1:1 baseline的5376个训练图/标签在新数据集中逐文件SHA一致；重复image+label SHA为0。
- 未读取holdout、未来收益或val误报；没有选择新置信度阈值；未promote。Owner于2026-08-11
  16:12 CST在对话中明确回复“允许 开始吧”，第二次3060训练已按冻结配方完整跑满40轮；
  训练与连续密度终局见`analysis/p2_owner_short_gold_center_hardneg_canary_20260811.md`。

## 数据统计

| 数据 | train | val | 说明 |
|---|---:|---:|---|
| positive | 1,143 | 202 | 与1:1 baseline完全相同 |
| easy negative | 1,143 | 200 | 与1:1 baseline完全相同 |
| Owner-long hard negative | 916 | 0 | Owner人工方向反类；只取train截止前且避开short±12根 |
| model-ranked background | 1,370 | 0 | 从6,858个train安全背景按baseline分数排序补齐 |
| 合计图片 | 4,572 | 402 | train负正比3:1 |

Owner-long初始1152行的排除：25行不在当前short训练币种宇宙、203行晚于train截止、6行重复目标、2行碰到short保护区；最终916行。

## 窗口分布合同

| W | train positive | hard target / actual |
|---:|---:|---:|
| 12 | 174 | 348 / 348 |
| 13 | 290 | 580 / 580 |
| 14 | 239 | 478 / 478 |
| 15 | 114 | 228 / 228 |
| 16 | 196 | 392 / 392 |
| 17 | 68 | 136 / 136 |
| 18 | 21 | 42 / 42 |
| 19 | 41 | 82 / 82 |
| **合计** | **1,143** | **2,286 / 2,286** |

这不是把框位置、延迟或形态参数重新写死；它只保证第二臂与第一臂在输入窗口分布上可比，实验变量仅为hard negatives。

## 模型排序背景的分数分布

排序使用已冻结baseline权重SHA-256 `da278820f2d96a64006d9ff6358b7c98faec52249ec8a6f4fe6bf55254fc65b4`，推理floor=0.001、NMS IoU=0.70。选择规则是“每个W按分数从高到低补足预先冻结的数量”，不是用新阈值裁剪。

| 指标 | 值 |
|---|---:|
| min / p10 / p25 | 0.0042 / 0.0124 / 0.0424 |
| p50 / p75 / p90 / max | 0.1803 / 0.5319 / 0.7122 / 0.8558 |
| ≥0.25（仅事后描述） | 622 / 1,370 = 45.40% |
| ≥0.35（仅事后描述） | 523 / 1,370 = 38.18% |
| 零分入选 | 0 |

0.25/0.35只描述冻结选择后的分布，没有用于挑样本，也不是新生产门。

## 与baseline同表对照

| 配置 | train正 | train easy负 | train hard负 | train负正比 | val正/负 | 状态 |
|---|---:|---:|---:|---:|---:|---|
| 1:1 easy baseline | 1,143 | 1,143 | 0 | 1.0 | 202 / 200 | 已训练；只用于挖hard negatives |
| 1:3 hard-negative arm | 1,143 | 1,143 | 2,286 | 3.0 | 202 / 200 | 已训练；连续密度仍失败 |

## 数据完整性

- base训练图/标签逐文件字节一致：5,376个；
- val图片：202正+200负=402；val标签同数，文件名与SHA全部一致；
- 新hard negatives全部只进入`images/train`/`labels/train`；
- duplicate image+label SHA：0；
- 背景候选完整窗口在train时间块内，并避开所有Owner多空框±12根；
- Owner-long输入结束时间≤`2026-03-13T18:30:00Z`，且避开Owner-short保护区；
- holdout读取：0；未来收益用于选择：0；val用于选择：0。

## 必报指标状态

- val AUC、置换检验p、top-decile毛/净收益、胜率、单特征基线：N/A。本轮是YOLO训练数据构建，尚未训练，也没有LightGBM排序层。
- 匹配随机对照组：N/A。本轮不产生方向性交易结果；下一次模型回放报告必须继续带同币×同时间块×同波动桶随机入场对照。
- 训练后必须与1:1 baseline同表报告val P/R/mAP、独立连续窗口event密度和FP/1000；不能仅凭自家val mAP晋升。

## 风险与诚实声明

- Owner-long是可靠的方向反类，但不是“所有相似结构都失败”的证明；其作用是教short-only模型拒绝镜像方向。
- 模型排序背景虽避开全部已知Owner框，历史标注仍不可能穷举市场中的每个真实形态；训练前应抽查最高分montage，防止把漏标真阳性大批写成负例。
- 本轮没有改增强、初始化、epoch、batch、学习率、val或阈值。第二臂训练必须沿用Stage A best初始化与baseline同一训练脚本，才满足单变量纪律。
- 本次第二轮3060训练已获得Owner逐次授权，仅覆盖run
  `owner_lsv2_short_gold_center_hardneg_r1_ft`；本报告不把启动训练表述成模型已修好。

## 200张逐图人工审核页

已生成独立审核页 `analysis/html/p2_owner_short_gold_center_hardneg_audit200_20260811.html`：

- 前100张为Owner-long方向反类，橙色竖带仅在审核页标出原long核心；
- 后100张为baseline分数最高的安全背景；
- 每张图独立展示并可点开原图，不是3张大拼图；
- 200/200图片引用存在；审核页不读取未来收益、holdout或val。

## 复现命令

```bash
PYTHONPATH=.:/Users/zhangzc/yoyo-trading .venv/bin/python \
  scripts/build_owner_short_gold_center_hardneg.py --mode prepare

PYTHONPATH=.:/Users/zhangzc/yoyo-trading .venv/bin/python \
  scripts/build_owner_short_gold_center_hardneg.py --mode mine \
  --device mps --batch 32

PYTHONPATH=.:/Users/zhangzc/yoyo-trading .venv/bin/python \
  scripts/build_owner_short_gold_center_hardneg.py --mode assemble

PYTHONPATH=.:/Users/zhangzc/yoyo-trading .venv/bin/python \
  scripts/build_owner_short_gold_center_hardneg.py --mode audit

PYTHONPATH=.:/Users/zhangzc/yoyo-trading .venv/bin/pytest -q \
  tests/test_build_owner_short_gold_center_hardneg.py \
  tests/test_build_owner_short_gold_center_dataset.py

python3 scripts/md_to_html.py \
  analysis/p2_owner_short_gold_center_hardneg_arm_20260811.md \
  --out-dir analysis/html
```

## 产物与下一步

- 候选与排序：`datasets/owner_short_gold_center_hardneg_candidates_r1/`
- 第二训练臂：`datasets/owner_short_gold_center_hardneg_r1/`
- machine-readable summary：`datasets/owner_short_gold_center_hardneg_r1/summary.json`
- 200张逐图审核页：`analysis/html/p2_owner_short_gold_center_hardneg_audit200_20260811.html`

run `owner_lsv2_short_gold_center_hardneg_r1_ft`已完成：Stage A best初始化、epochs40、
patience10、batch8、seed0、显式finetune、AdamW lr0=1e-4、warmup0.5，所有禁用增强均保持0。
Mac固定val mAP50/mAP50-95=0.8980/0.7405；独立pre-holdout连续canary将事件从1,464/day降至
662/day（-54.78%），改善明确但密度仍失败。当前权重不得promote；下一步先审核剩余331事件，
不能自动全写负例。

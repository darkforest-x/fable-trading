# Owner审核结论：当前模型约20%精确命中

## Executive Summary

- **331个事件已全部完成Owner裁决且数据可信。** 协议、源事件SHA、ID集合和声明计数全部一致，
  0条未确认；结果为66个“对”、11个“框偏”、254个“不对”。
- **当前12小时canary的精确事件命中率为19.94%。** 若把“形态对但框偏”也计作语义命中，
  语义precision为23.26%；其余76.74%是Owner明确否决的形态。
- **置信度有排序能力，但阈值不能解决问题。** `peak≥0.8`时语义precision仅50.63%；提高到0.9
  虽达到81.25%，却只保留77个语义正例中的13个（16.88%），不能靠抬门槛冒充模型修复。
- **254个误报只能作为错误参考尺，不能直接回流当前训练。** 它们全部位于冻结val之后；直接加入
  会让训练时间晚于验证时间。下一步应在原train截止时间内挖出同类负例，再建单变量第三臂。

## 四分之三触发是Owner明确误报

本次单位是经“同币种 + 核心中点±5根”去重后的事件，不是YOLO原始框、订单或收益样本。331个事件
来自2026年5月3日00:15–12:00 UTC的完整连续canary，没有抽样丢弃；Owner逐张查看当时因果窗口和
独立未来对照后完成三项裁决。

| Owner裁决 | 事件 | 覆盖币种 | 占331事件 | 解释 |
|---|---:|---:|---:|---|
| 对 | 66 | 49 | 19.94% | 形态与预测框都正确 |
| 框偏 | 11 | 11 | 3.32% | 形态正确，但核心框位置不准 |
| 不对 | 254 | 125 | 76.74% | 不是Owner目标形态 |
| 合计 | 331 | 140 | 100.00% | 0条未确认 |

将“对 + 框偏”合并后，模型找到目标形态的语义precision为77/331 = **23.26%**。在这77个语义
命中里，66个框正确，几何正确率为 **85.71%**；当前主要矛盾仍是把普通形态识别为目标，而不是
框偏本身。

## 置信度能排序，但无法形成可用门槛

真目标的事件峰值置信度中位数为0.830，明确负例为0.563，说明分数有排序信息。但高分区仍包含
大量误报；门槛越高，precision改善的代价是绝大多数真形态一起消失。

| peak conf门 | 保留事件 | 精确precision | 语义precision | 保留语义正例 / 77 |
|---:|---:|---:|---:|---:|
| ≥0.50 | 215 | 26.05% | 30.70% | 66 / 77（85.71%） |
| ≥0.60 | 181 | 29.28% | 33.70% | 61 / 77（79.22%） |
| ≥0.70 | 126 | 36.51% | 42.86% | 54 / 77（70.13%） |
| ≥0.80 | 79 | 45.57% | 50.63% | 40 / 77（51.95%） |
| ≥0.90 | 16 | 68.75% | 81.25% | 13 / 77（16.88%） |

这里的“保留语义正例”只表示331个已审核事件内的保留率，不是全市场recall；项目仍没有独立完整
真形态分母。结论只用于排除“调高conf即可解决”的路线，**不授权修改冻结阈值**。

按peak confidence从高到低看，前50个事件语义precision为62%，但第101–200名已经降至15%，
第201–331名仅10.69%。这说明置信度适合决定人工审核顺序和挖负例优先级，不足以直接做生产裁决。

| peak排名 | 事件 | 对 | 框偏 | 不对 | 语义precision |
|---|---:|---:|---:|---:|---:|
| 1–50 | 50 | 29 | 2 | 19 | 62.00% |
| 51–100 | 50 | 12 | 5 | 33 | 34.00% |
| 101–200 | 100 | 12 | 3 | 85 | 15.00% |
| 201–331 | 131 | 13 | 1 | 117 | 10.69% |

## 254个误报是参考尺，不是当前训练样本

| 时间边界 | UTC时间 |
|---|---|
| 冻结train最后窗口 | 2026-03-13 18:30 |
| 冻结val最后窗口 | 2026-05-02 23:45 |
| Owner审核事件范围 | 2026-05-03 00:15–12:00 |

331个审核事件被预注册为`preholdout_postval_canary`，全部严格晚于冻结val。若把254个负例直接加入
训练，同时继续报告原val结果，就会形成“训练数据晚于验证数据”的时间倒置；即使图片本身严格因果，
评估仍然失真。因此254行保持`training_eligible=false`，66个目标和11个框偏也不会被误写成负例。

## 推荐下一步

1. 冻结本次331条Owner回执，作为错误语义参考和后续独立密度对照，不再修改标签。
2. 只在冻结train截止时间（不晚于2026-03-13 18:30 UTC）内连续扫描，寻找与254个误报相似的
   高分假形态；不得读取未来收益、val或holdout选择样本。
3. 对train-time候选按币种、时间、窗口长度和视觉相似簇去重，再做一轮小规模Owner确认；禁止把
   254张post-val图片直接复制进训练集。
4. 第三训练臂保持1,143正例、1,143 easy negative、2,286 hard negative、冻结val和训练配方不变，
   只用已确认的train-time难负例替换相同W桶的旧model-ranked背景，维持单变量纪律。
5. 新权重先在另一个未使用的post-train、pre-holdout连续时间块验证密度和Owner precision；通过后
   才讨论新的holdout授权，仍不得自动promote。

## Further Questions

- train时间块内能否找到足量、跨币种且不碰Owner框±12根的同类误报？数量不足时应扩时间覆盖，
  不能降低语义标准凑数。
- 11个“框偏”是否需要Owner逐张重框？它们不影响下一轮负例臂，可延后到正例几何专项处理。
- 后续连续验证应优先覆盖哪几种行情状态，才能证明误报下降不是只适配5月3日单一市场环境？

## Caveats and Assumptions

- 19.94% / 23.26%只代表一个12小时、140币的post-val canary，不等于长期全市场precision。
- Owner审核看到了最多48根未来K线；未来只用于人工确认形态结果，没有进入因果图片、模型特征或标签
  几何，但裁决本身属于事后形态语义。
- 事件以核心中点±5根去重，仍可能存在跨更长距离的同趋势延续；延续率需另做关系层分析。
- 没有完整真形态事件集，因此本轮不能报告市场recall、F1或FP/1000真金标口径。
- 未读取holdout、未改阈值、未构建新训练集、未启动训练、未改ACTIVE、未promote或下单。

## 数据质量与必报指标

- 协议：`owner_short_hardneg_canary_review331_v3_20260811`，匹配。
- 源事件SHA：`e81ec8088a77df1203da5cee8f18461ab1aae0426c087985ac23da78a540898e`，匹配。
- Owner回执SHA：`fa7f39c537fbeeee2597cce06d32f6b7f6c0fdc7edffaadf993b040b89171a9d`。
- 331个review_id和event_id均唯一，决策ID一对一覆盖；声明计数重算一致；标签值全部合法。
- 全量测试：**646 passed，2 skipped**；14条均为既有matplotlib/pyparsing弃用警告。
- val AUC、置换检验p、top-decile毛/净收益、胜率、单特征基线、匹配随机对照：N/A；本轮是YOLO
  事件语义审核，没有LightGBM排序、收益实验或订单。

## 复现命令与交付物

```bash
PYTHONPATH=. .venv/bin/python scripts/ingest_owner_short_canary_review.py \
  --review-json \
  analysis/output/owner_short_gold_center_hardneg_canary_review331_v3/owner_review_decisions.json

PYTHONPATH=.:/Users/zhangzc/fable-trading .venv/bin/pytest -q tests
python3 scripts/md_to_html.py \
  analysis/p2_owner_short_hardneg_canary_owner_review_20260811.md \
  --out-dir analysis/html
```

- Owner原始决策：
  `analysis/output/owner_short_gold_center_hardneg_canary_review331_v3/owner_review_decisions.json`
- 一对一带标签manifest：
  `analysis/output/owner_short_gold_center_hardneg_canary_review331_v3/owner_review_labeled_manifest.jsonl`
- 机器统计与质量门：
  `analysis/output/owner_short_gold_center_hardneg_canary_review331_v3/owner_review_summary.json`

# P1 — 固定 W10 金标的标签错误率（2026-08-20）

> 实验：`exp-p1-gold-label-quality-cleanlab-v1`
> **只推理，未训练；未读 holdout；未改 `training_eligible`。**

## 问的是什么

协议 17.6 要求金标在获得训练资格前必须有一个 **DIRECT 抽检错误率**。
迁移产出 DIRECT=0，所以这个数**从来没存在过**，而它是
固定 W10 金标（SIGNAL 1,247 / NO_SIGNAL 1,402）`training_eligible=false`
的**唯一**原因——其余门全过。

本轮用 confident learning 从已训分类器的样本外预测里**估计**它，
而不是靠人工随机抽样去猜。

## 复现命令

```bash
# 两个隔离 venv：ultralytics 要 numpy 2.x，cleanlab 要 numpy <2，不能共存
python3 -m venv /tmp/fable_infer_venv
/tmp/fable_infer_venv/bin/pip install torch==2.8.0 torchvision==0.23.0 ultralytics==8.4.89 numpy==2.0.2 Pillow==11.3.0
python3 -m venv /tmp/fable_eval_venv
/tmp/fable_eval_venv/bin/pip install -r requirements-eval.txt "numpy<2"

/tmp/fable_infer_venv/bin/python tools/datasets/score_gold_label_quality.py --stage predict \
  --dataset-root ~/fable-trading/datasets/fixed_w10_core4_confirm1_v1/classification \
  --weights ~/fable-trading/analysis/output/fixed_w10_cls_holdout3d_20260813/best.pt \
  --out experiments/active/exp-p1-gold-label-quality-cleanlab-v1

/tmp/fable_eval_venv/bin/python tools/datasets/score_gold_label_quality.py --stage audit \
  --out experiments/active/exp-p1-gold-label-quality-cleanlab-v1
/tmp/fable_eval_venv/bin/python tools/datasets/score_gold_label_quality.py --stage null-control \
  --out experiments/active/exp-p1-gold-label-quality-cleanlab-v1 --splits test val
```

## 数据统计

| 项 | 值 |
|---|---|
| 数据集 | `fixed_w10_core4_confirm1_v1/classification` |
| manifest SHA | `20686feba41d15b82e34109402840c2d640fe1e2daea0392b35e1ea79320a7fc` |
| 分割 | train 1,849 / **val 350** / **test 450** |
| val 类别 | SIGNAL 179 / NO_SIGNAL 171 |
| test 类别 | SIGNAL 199 / NO_SIGNAL 251 |
| 权重 | `analysis/output/fixed_w10_cls_holdout3d_20260813/best.pt` |
| 权重 SHA256 | `18bcb5988e6dd36bdf2fc8a1a22d3ad66ab78b777a1d02c88080c937e98d0541`（脚本硬断言，与 `backtest_fixed_w10_cls_holdout3d.py` 的 `EXPECTED_WEIGHTS_SHA256` 一致） |
| 预处理 | `WhiteLetterbox(960) + ToTensor`，逐字节抄自训练脚本 |
| 时间范围 | 全部 pre-holdout（holdout 起点 2026-05-04；test 最晚 2026-04) |
| holdout 读取 | **0** |

## 结果

| 分割 | 状态 | n | 疑似错标 | **错误率** | 模型对给定标签准确率 |
|---|---|---|---|---|---|
| val | **被早停用过**（模型经模型选择间接看过标签） | 350 | 11 | 3.14% | 91.14% |
| **test** | **从未评估过** | 450 | **28** | **6.22%** | **89.33%** |

**头条数字是 test 的 6.22%。** val 的 3.14% 偏乐观，因为那正是模型被调向的那批标签——
两者刻意分开报告、从不合池。

### 零假设对照

「6.22% 算高还是低」没有尺度就无法回答。把标签随机打乱、概率不变，再跑同一个方法：

| 分割 | 真实标签被标记 | 标签打乱后被标记（10 次均值） | 比值 |
|---|---|---|---|
| test | 28（6.22%） | 227.5（**50.6%**，范围 209–240） | **0.123** |
| val | 11（3.14%） | 170.5（48.7%） | 0.065 |

**真实标签的疑似错标率只有纯噪声的 1/8。** 这个方法在这批数据上确实在区分东西，
6.22% 是一个真实的低值，不是方法自身的产物。

### 与模型分歧的关系

| test | 数量 | 占比 |
|---|---|---|
| 模型直接判错（argmax ≠ 标签） | 48 | 10.67% |
| cleanlab 判定为疑似错标 | **28** | 6.22% |

cleanlab 只从 48 个分歧里挑出最有把握的 28 个。**剩下 20 个是模型没把握的分歧**——
那更可能是模型弱，不是标签错。

### 逐类：负例的疑似错标率是正例的两倍

| 分割 | SIGNAL | NO_SIGNAL |
|---|---|---|
| test | 8 / 199 = **4.0%** | 20 / 251 = **8.0%** |
| val | 3 / 179 = 1.7% | 8 / 171 = 4.7% |

## 解读

1. **6.22% 是一个可以拿去过协议 17.6 的候选数字**，且它比随机基线低一个数量级。
   但它是**估计**，不是裁决——见下方风险第 1 条。

2. **负例的噪声是正例的两倍，而这与建库过程吻合。**
   yoyo-trading 的 HANDOFF 记着：2026-08-13 owner 授权
   「把已有负例池收进 `review/state.jsonl`，**不再一张张标**」，一次收进 1,395 个
   （易负例池 1,256 + 一键无核 139）。**批量收进来的那一半，噪声正好高一倍。**
   这不是巧合，是可预期的后果，而现在有数字了。

3. **test 准确率 89.33% 是这个项目第一次拿到的 test 数字。**
   yoyo-trading 的 HANDOFF 明写「test **没跑**」。val 91.4% → test 89.33%，
   泛化差距不大，诚实。

4. **val 与 test 的错误率差了一倍（3.14% vs 6.22%），差在污染而不是数据。**
   同一批标注流程、同一时期的数据，唯一区别是 val 被早停用过。
   **这是「不要用调过参的那一份来估计质量」的一个干净演示。**

## 与上一版本对照

本项目此前**没有任何**金标错误率的数字——DIRECT=0 就是「没测过」。
所以本表无同表前值可比，这是第 1 版。

## 关于必报指标的说明

CLAUDE.md 要求方向性策略的每张结果表都带匹配随机对照组、置换检验、
top-decile 净收益。**本轮不是方向性策略，是一次标签质量测量，没有收益可言。**
对应位置的严谨性由**零假设对照**（标签打乱）承担，见上。
不编造不适用的指标。

## 风险与诚实声明

1. **这是模型相关的估计，不是标签真值。** cleanlab 标记的是「模型有把握地不同意
   这个标签」。本项目已知这一族模型在盘口很弱（旧 detector tip 复现率 9–10%），
   所以**被标记 = 标签错 ∪ 模型错**，两者混在一起。
   6.22% 应读作**筛查上界**，不是已证实的错误率。
2. **正确用法是当作复审队列，不是当作结论。** 协议 17.6 要的 DIRECT 抽检，
   传统做法是随机抽 100 张让 owner 看。现在可以只看这 28 张最可疑的——
   **同样的 owner 时间，得到紧得多的估计**。逐图清单已产出：
   `experiments/active/exp-p1-gold-label-quality-cleanlab-v1/per_image_test.jsonl`。
3. **n=450 不大。** 6.22% 的 Wilson 95% 区间约为 [4.3%, 8.9%]。
   区间上沿接近 9%，能不能过 17.6 取决于阈值定在哪——**那是 owner 决策**。
4. **train 分割（1,849）没有测。** 它没有样本外预测，要测必须做交叉验证训练，
   而 P0/P1 阶段禁止新训练。所以本轮覆盖的是 800/2,649 = 30% 的金标。
5. **模型只训到 best epoch 3、23/100 早停。** 一个欠训模型的「有把握的分歧」
   与一个充分训练模型的不是一回事。这个数字会随模型变化。
6. **未改动任何东西**：未训练、未 promote、未读 holdout、未写 `training_eligible`、
   未动 ACTIVE。产物全部落在 `experiments/active/` 下。

## 下一步选项（需 owner 决策）

- **A（推荐）：复审那 28 张。** owner 逐张 YES/NO，得到 test 上的真实 DIRECT 错误率，
  且顺便修掉真的错标。成本约 28 张 × 数秒。这是把估计变成裁决的最短路径。
- **B：先扩到 train 分割。** 需要交叉验证训练 → **突破 P0/P1 的训练禁令**，需要单独批准。
- **C：只复审 20 张负例。** 若只关心「批量收进来的负例池有多脏」，这是最省时间的切法。
- **D：不动，把 6.22% 记为筛查值。** 但 `training_eligible` 仍然过不了 17.6，
  因为协议要的是 DIRECT 抽检，不是模型估计。

**无论选哪个，`training_eligible` 的翻转都是 owner 的决定，不在本轮范围内。**

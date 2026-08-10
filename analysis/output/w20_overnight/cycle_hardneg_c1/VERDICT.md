# VERDICT — cycle_hardneg_c1/weights/best.pt

**状态：未裁决。禁止晋升。**

写于 2026-08-10。不是"判死"，是**从未经过唯一有效的那道门**。

## 有的东西：val 指标（不构成裁决依据）

val 构成：正 405 · 空背景 405 · 硬负 553。相对 cold_v1 确有改善：

| conf | hardneg_c1 F1 / P / 硬负误火 | cold_v1 F1 / P / 硬负误火 |
|---|---|---|
| 0.20 | 0.418 / 0.443 / 0.047 | 0.269 / 0.267 / 0.289 |
| 0.30 | 0.303 / 0.545 / 0.015 | 0.189 / 0.634 / 0.007 |

硬负误火从 0.289 降到 0.047 是真的改善。**但按铁律 12，这些数字永不作裁决。**
详见 `analysis/output/w20_overnight/eval_hardneg_c1_vs_cold.md`。

## 缺的东西：唯一有效的门

铁律 12 规定检测器晋升唯一门 = **真 tip 金标 + tip-smoke**。本权重：

- 没有跑过全市场 tip-replay 回测（cold 跑过，结果见隔壁 `cycle_0_.../VERDICT.md`）
- 前向 shadow 只累计了 **78 笔**（`analysis/output/forward_log_w20_midbox_shadow.csv`），
  目标是 100 笔新鲜裁决，未达标；最后写入 2026-08-07 17:22，此后停摆

## 同样的污染

与 cold 同源：`build_w20_midbox_dataset.py` 无 holdout 过滤。实测 2635 个正样本里
**246 个（9.3%）窗口右端落在 holdout 期**，209 个在 train、37 个在 val。
即便补跑 tip 回测，holdout 段也不是干净的样本外证据。

## 要动它之前必须做的

1. 数据集换成有 holdout 过滤的版本重训（`datasets/local_signal_v2_stageb` 是正确范例）
2. 跑 pre-holdout 段 tip-replay + matched control + 置换检验
3. holdout 评估需 owner 逐次批准并记账

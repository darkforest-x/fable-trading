# ML 层优化扫描（YOLO 判断池，val-only）
**日期**：2026-07-15  
**纪律**：未加 `--eval-holdout`；未改 `features.py` / 冻结模型 / `forward_log`。  
**主指标**：val top-decile 扣 0.2% 往返后净收益（AUC 仅参考）。

## 复现

```bash
python3 scripts/ml_layer_opt_sweep.py --data data/judgment_yolo_swap.csv --tag ml_opt_yolo
```

## 数据

| 项 | 值 |
|---|---|
| 数据集 | `data/judgment_yolo_swap.csv` |
| train / val / holdout(n only) | 1382 / 349 / 640 |
| val 正类率 | 0.4212 |
| train 时间 | 2025-06-08 14:45:00+00:00 → 2026-03-12 00:45:00+00:00 |
| val 时间 | 2026-03-12 23:30:00+00:00 → 2026-05-03 01:30:00+00:00 |

## 结果表（按 top-decile 净收益排序）

| 排名 | 变体 | AUC | perm p | top-n | top 毛 | top 净@0.2% | top 胜率 | q90-n | q90 净@0.2% | best_iter |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | `reg_realized_ret` | 0.8156 | 0.001 | 34 | +0.03123 | **+0.02923** | 0.735 | 35 | +0.02908 | 38 |
| 2 | `abs_ret_weight` | 0.8211 | 0.001 | 34 | +0.02876 | **+0.02676** | 0.824 | 35 | +0.02697 | 88 |
| 3 | `deeper` | 0.8207 | 0.001 | 34 | +0.02823 | **+0.02623** | 0.882 | 35 | +0.02603 | 100 |
| 4 | `slow_lr_0p02` | 0.8169 | 0.001 | 34 | +0.02789 | **+0.02589** | 0.853 | 35 | +0.02612 | 136 |
| 5 | `baseline` | 0.8172 | 0.001 | 34 | +0.02641 | **+0.02441** | 0.853 | 35 | +0.02431 | 67 |
| 6 | `seed_ensemble_3` | 0.8233 | 0.001 | 34 | +0.02587 | **+0.02387** | 0.882 | 35 | +0.02360 | 51 |
| 7 | `strong_reg` | 0.8183 | 0.001 | 34 | +0.02563 | **+0.02363** | 0.824 | 35 | +0.02363 | 84 |
| 8 | `scale_pos_weight` | 0.8161 | 0.001 | 34 | +0.02488 | **+0.02288** | 0.882 | 35 | +0.02204 | 48 |
| 9 | `top15_features` | 0.8015 | 0.001 | 34 | +0.02285 | **+0.02085** | 0.794 | 35 | +0.02088 | 42 |
| 10 | `recency_weight_60d` | 0.8115 | 0.001 | 34 | +0.02220 | **+0.02020** | 0.824 | 35 | +0.02132 | 66 |
| 11 | `baseline_logreg_ma_spread` | 0.4757 | 0.748 | 34 | +0.01032 | **+0.00832** | 0.618 | 35 | +0.00814 | None |

## 对照基线

- baseline top 净：`+0.02441`  
- 最优变体：`reg_realized_ret` → `+0.02923`  
- Δ：`+0.00482`  

## 各变体说明

- **baseline**：Current LGB_PARAMS binary classifier
- **strong_reg**：num_leaves=7, min_child=50, l2=5, feat_frac=0.7
- **deeper**：num_leaves=31, min_child=20, lr=0.03
- **scale_pos_weight**：balance class frequency via scale_pos_weight
- **recency_weight_60d**：exp decay sample weight half-life 60d
- **abs_ret_weight**：sample weight ∝ floor+|realized_ret|
- **reg_realized_ret**：predict realized_ret; rank by predicted return
- **seed_ensemble_3**：average 3 seeds {42,7,2026}
- **top15_features**：retrain on top-15 gain features from baseline
- **slow_lr_0p02**：lr=0.02, rounds=1200, es=80
- **baseline_logreg_ma_spread**：single-feature logistic baseline

## 解读

最优变体相对 baseline 的 top 净提升 **+0.00482**（>20bp）。若 p 仍 <0.01 且 q90 gate 不塌，可作为挑战者做影子/冻结候选；**不得**仅凭 val 切换生产冻结。

> **异常警示**：val AUC ≥ 0.75（YOLO 池已知现象）。优先怀疑选择偏置/小样本，而非「模型真的很强」。经济指标方差大，前向 100 笔才是硬闸门。

## 风险与诚实声明

- 本扫描 **未评估 holdout**，未消耗 holdout 配额。
- val 已被多次选型使用，数字只用于排序，不得宣称样本外绩效。
- 未写入 `models/frozen*`，未改前向配置。
- 多变体并行扫描，存在多重比较；胜出者需要独立前向验证。

## 下一步选项（需 owner 决策的已标注）

1. 若最优 Δ 大且稳定：用该配置 **另打 tag 冻结影子**，不替换 ACTIVE（owner 决策）。
2. 若全军覆没或噪声级：停止 ML 超参军备竞赛；资源转向 YOLO 重标/重训与前向密度。
3. 可选：在 **规则 expanded 池** 上重复本扫描（更大 n，AUC 更低，更考验排序）。
4. **不要** 因本结果消耗 holdout。

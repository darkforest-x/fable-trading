# ML 层优化扫描（YOLO 判断池，val-only）
**日期**：2026-07-15  
**纪律**：未加 `--eval-holdout`；未改 `features.py` / 冻结模型 / `forward_log`。  
**主指标**：val top-decile 扣 0.2% 往返后净收益（AUC 仅参考）。

## 复现

```bash
python3 scripts/ml_layer_opt_sweep.py --data data/judgment_dataset_v2_expanded.csv --tag ml_opt_rules_expanded
```

## 数据

| 项 | 值 |
|---|---|
| 数据集 | `data/judgment_dataset_v2_expanded.csv` |
| train / val / holdout(n only) | 6367 / 1598 / 2214 |
| val 正类率 | 0.4262 |
| train 时间 | 2025-06-04 16:15:00+00:00 → 2026-03-24 22:45:00+00:00 |
| val 时间 | 2026-03-25 17:15:00+00:00 → 2026-05-03 03:30:00+00:00 |

## 结果表（按 top-decile 净收益排序）

| 排名 | 变体 | AUC | perm p | top-n | top 毛 | top 净@0.2% | top 胜率 | q90-n | q90 净@0.2% | best_iter |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | `reg_realized_ret` | 0.5669 | 0.001 | 159 | +0.00506 | **+0.00306** | 0.516 | 160 | +0.00318 | 70 |
| 2 | `deeper` | 0.5606 | 0.001 | 159 | +0.00303 | **+0.00103** | 0.503 | 160 | +0.00108 | 32 |
| 3 | `baseline` | 0.5647 | 0.001 | 159 | +0.00301 | **+0.00101** | 0.509 | 160 | +0.00104 | 19 |
| 4 | `abs_ret_weight` | 0.5596 | 0.001 | 159 | +0.00298 | **+0.00098** | 0.497 | 160 | +0.00093 | 17 |
| 5 | `strong_reg` | 0.5721 | 0.001 | 159 | +0.00274 | **+0.00074** | 0.497 | 160 | +0.00080 | 64 |
| 6 | `seed_ensemble_3` | 0.5664 | 0.001 | 159 | +0.00271 | **+0.00071** | 0.491 | 160 | +0.00077 | 28 |
| 7 | `slow_lr_0p02` | 0.5596 | 0.001 | 159 | +0.00267 | **+0.00067** | 0.497 | 160 | +0.00071 | 73 |
| 8 | `recency_weight_60d` | 0.5535 | 0.001 | 159 | +0.00242 | **+0.00042** | 0.472 | 160 | +0.00037 | 15 |
| 9 | `top15_features` | 0.5652 | 0.001 | 159 | +0.00223 | **+0.00023** | 0.465 | 160 | +0.00016 | 33 |
| 10 | `scale_pos_weight` | 0.5609 | 0.001 | 159 | +0.00222 | **+0.00022** | 0.472 | 160 | +0.00018 | 13 |
| 11 | `baseline_logreg_ma_spread` | 0.4619 | 0.995 | 159 | +0.00004 | **-0.00196** | 0.321 | 160 | -0.00203 | None |

## 对照基线

- baseline top 净：`+0.00101`  
- 最优变体：`reg_realized_ret` → `+0.00306`  
- Δ：`+0.00205`  

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

最优变体相对 baseline 的 top 净提升 **+0.00205**（>20bp）。若 p 仍 <0.01 且 q90 gate 不塌，可作为挑战者做影子/冻结候选；**不得**仅凭 val 切换生产冻结。

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

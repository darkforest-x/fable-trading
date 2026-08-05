# ML 层优化扫描（YOLO 判断池，val-only）
**日期**：2026-07-15  
**纪律**：未加 `--eval-holdout`；未改 `features.py` / 冻结模型 / `forward_log`。  
**主指标**：val top-decile 扣 0.2% 往返后净收益（AUC 仅参考）。

## 复现

```bash
python3 scripts/ml_layer_opt_sweep.py --data data/swap_replication/swap_tp5_sl2.csv --tag ml_opt_swap_tp5
```

## 数据

| 项 | 值 |
|---|---|
| 数据集 | `data/swap_replication/swap_tp5_sl2.csv` |
| train / val / holdout(n only) | 6027 / 1510 / 1709 |
| val 正类率 | 0.3212 |
| train 时间 | 2025-06-05 05:15:00+00:00 → 2026-03-16 21:00:00+00:00 |
| val 时间 | 2026-03-17 23:15:00+00:00 → 2026-05-03 01:15:00+00:00 |

## 结果表（按 top-decile 净收益排序）

| 排名 | 变体 | AUC | perm p | top-n | top 毛 | top 净@0.2% | top 胜率 | q90-n | q90 净@0.2% | best_iter |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | `reg_realized_ret` | 0.5586 | 0.001 | 151 | +0.00504 | **+0.00304** | 0.404 | 151 | +0.00304 | 12 |
| 2 | `strong_reg` | 0.5632 | 0.001 | 151 | +0.00261 | **+0.00061** | 0.404 | 151 | +0.00061 | 31 |
| 3 | `seed_ensemble_3` | 0.5591 | 0.001 | 151 | +0.00248 | **+0.00048** | 0.397 | 151 | +0.00048 | 12 |
| 4 | `scale_pos_weight` | 0.5424 | 0.006 | 151 | +0.00235 | **+0.00035** | 0.364 | 214 | +0.00029 | 3 |
| 5 | `slow_lr_0p02` | 0.5564 | 0.001 | 151 | +0.00222 | **+0.00022** | 0.378 | 151 | +0.00022 | 41 |
| 6 | `deeper` | 0.5521 | 0.002 | 151 | +0.00196 | **-0.00004** | 0.358 | 151 | -0.00004 | 20 |
| 7 | `recency_weight_60d` | 0.5561 | 0.002 | 151 | +0.00186 | **-0.00014** | 0.351 | 151 | -0.00014 | 14 |
| 8 | `top15_features` | 0.5419 | 0.007 | 151 | +0.00173 | **-0.00027** | 0.364 | 153 | -0.00024 | 9 |
| 9 | `abs_ret_weight` | 0.5440 | 0.004 | 151 | +0.00133 | **-0.00067** | 0.325 | 154 | -0.00069 | 14 |
| 10 | `baseline_logreg_ma_spread` | 0.4860 | 0.802 | 151 | +0.00095 | **-0.00105** | 0.272 | 151 | -0.00105 | None |
| 11 | `baseline` | 0.5601 | 0.001 | 151 | +0.00086 | **-0.00114** | 0.325 | 151 | -0.00114 | 25 |

## 对照基线

- baseline top 净：`-0.00114`  
- 最优变体：`reg_realized_ret` → `+0.00304`  
- Δ：`+0.00418`  

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

最优变体相对 baseline 的 top 净提升 **+0.00418**（>20bp）。若 p 仍 <0.01 且 q90 gate 不塌，可作为挑战者做影子/冻结候选；**不得**仅凭 val 切换生产冻结。

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

# short 100×6m 回归 — 5-fold walkforward（发现级，未 holdout）

**日期**：2026-07-24  
**性质**：对 `p2b_yolo_short_100_6m_reg` 同池做时间 walkforward，检验单切 top-decile 净 +0.471% 是否稳健。  
**纪律**：**无** `--eval-holdout`、**不** promote、**不**改 ACTIVE / TP·SL / 成本。

## 复现

指标 JSON：

```text
analysis/output/p2b_yolo_short_100_6m_reg_walkforward.json
```

池：`data/judgment_yolo_owner_side_short_100_6m.csv`（dev_n=25532，signal < holdout−purge）。  
成本：与 `train.py` 一致 **0.2%** 往返（LEGACY_P0）。  
切法：与 30×6m walkforward 相同 expanding 比例边（0.40/0.52/…/1.00）。

## 结果

| fold | val 窗（约） | n_val | Spearman | top-decile 净 | top-n | label 胜率 | best_it |
|---:|---|---:|---:|---:|---:|---:|---:|
| 1 | 01-14 → 02-04 | 3064 | +0.004 | **+0.229%** | 306 | 0.23 | 1 |
| 2 | 02-04 → 02-25 | 3064 | −0.005 | **−0.009%** | 306 | 0.15 | 1 |
| 3 | 02-25 → 03-19 | 3064 | −0.016 | **+0.372%** | 306 | 0.26 | 1 |
| 4 | 03-19 → 04-10 | 3064 | −0.083 | **+0.142%** | 306 | 0.27 | 1 |
| 5 | 04-10 → 05-03 | 3064 | +0.047 | **+0.790%** | 306 | 0.39 | 14 |

汇总对照：

| 量 | 100×6m | 30×6m |
|---|---:|---:|
| 单切 top-decile 净 | +0.471% | +0.371% |
| walkforward **net_mean** | **+0.305%** | +0.336% |
| net_min | **−0.009%** | −0.513% |
| rho_mean | **−0.010** | +0.028 |
| all_folds_net_positive | **false** | false |
| all_folds_rho_positive | **false** | false |

## 解读

1. **均值仍略正**，与单切同号——不是完全抹平。  
2. 负折幅度比 30 池小（接近 0 而非 −0.5%），但 **ρ 全面更差**（均值转负）→ 排序稳定性没有随扩样改善。  
3. 4/5 折 `best_iteration=1`：early-stop 几乎没学到结构；折 5 单独抬升贡献了大部分正净。  
4. 结论与单切报告一致：**间歇/弱边，未达稳健级**；扩 100 币 **不能**当作晋升或障碍扫参的通行证。

## 风险与诚实声明

- 发现级；未碰 holdout；检测 tip_v1b 未晋升。  
- fold 切法对齐 30 池 / freeze 风格 expanding 窗。  
- 成本 0.2%；实盘 maker 更低会抬绝对净，**不**改变「排序弱 + 折间依赖」形态。

## 下一步

按 S2 决策树 → **停扩样叙事**，转 S3（Owner 1000 目视 / 真 tip 金标）或复核信号定义。  
障碍 / holdout#8 / promote **须另批**；默认不做。

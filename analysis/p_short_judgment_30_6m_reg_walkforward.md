# short 30×6m 回归 — 5-fold walkforward（发现级，未 holdout）

**日期**：2026-07-24  
**性质**：对 `p2b_yolo_short_30_6m_reg` 同池做时间 walkforward，检验单切 top-decile 净 +0.371% 是否稳健。  
**纪律**：**无** `--eval-holdout`、**不** promote、**不**改 ACTIVE / TP·SL / 成本。

## 复现

指标 JSON 已落盘（前会话产出）：

```text
analysis/output/p2b_yolo_short_30_6m_reg_walkforward.json
```

池：`data/judgment_yolo_owner_side_short_30_6m.csv`（n≈7498–7519，signal ≤2026-05-03）。  
成本：与 `train.py` 一致 **0.2%** 往返（LEGACY_P0）。

## 结果

| fold | val 窗（约） | n_val | Spearman | top-decile 净 | top-n | label 胜率 |
|---:|---|---:|---:|---:|---:|---:|
| 1 | 01-14 → 02-04 | 899 | +0.035 | **+0.649%** | 89 | 0.36 |
| 2 | 02-04 → 02-26 | 900 | −0.017 | **−0.513%** | 90 | 0.08 |
| 3 | 02-26 → 03-19 | 900 | +0.002 | **+1.118%** | 90 | 0.34 |
| 4 | 03-19 → 04-11 | 900 | −0.021 | **−0.106%** | 90 | 0.22 |
| 5 | 04-11 → 05-03 | 900 | +0.140 | **+0.530%** | 90 | 0.32 |

汇总：

| 量 | 值 |
|---|---:|
| 单切基线 top-decile 净 | +0.371% |
| walkforward **net_mean** | **+0.336%** |
| net_min | **−0.513%** |
| rho_mean | +0.028 |
| all_folds_net_positive | **false** |
| all_folds_rho_positive | **false** |

## 解读

1. **均值仍略正**，与单切同量级——不是完全噪声抹平。  
2. **2/5 折净负**；折 2 几乎无标签胜率（0.08）→ 排序在部分 regime **失效甚至反害**。  
3. Spearman 多数接近 0；仅最近一折（折 5）抬到 0.14——与单切 0.15 同源，**时间上不均**。  
4. 结论：**30×6m short 回归 = 间歇正边，不是稳健可部署边。** 扩 100×6m 的动机仍是「看样本与宇宙是否稳住」，不是已过确认门。

## 风险与诚实声明

- 发现级；未碰 holdout；检测 tip_v1b 未晋升。  
- fold 切法对齐 freeze 风格 expanding 窗，非独立同分布。  
- 成本仍用 0.2%；实盘 maker 0.06% 会改善绝对值，**不**改变「间歇」形态。

## 下一步

1. **进行中**：100 流动性币 × 6m 同构扫池 + 回归（Owner 已批扩样本；不 kill lock）。  
2. 100 池训完后同样报单切 +（可选）walkforward；**仍不 promote**。  
3. 障碍 / holdout / 晋升须 Owner 另批。

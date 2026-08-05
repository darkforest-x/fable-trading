# ML 层可优化方向 — 实测扫描总结

**日期**：2026-07-15  
**纪律**：train/val only，三池均未评估 holdout；未改 `features.py` 主特征表、未写冻结模型、未动 `forward_log`。  
**主指标**：val top-decile 扣 0.2% 往返成本后净收益（AUC 仅参考）。

## 复现

```bash
cd /Users/zhangzc/fable-trading
PYTHONPATH=. python3 scripts/ml_layer_opt_sweep.py --data data/judgment_yolo_swap.csv --tag ml_opt_yolo
PYTHONPATH=. python3 scripts/ml_layer_opt_sweep.py --data data/judgment_dataset_v2_expanded.csv --tag ml_opt_rules_expanded
PYTHONPATH=. python3 scripts/ml_layer_opt_sweep.py --data data/swap_replication/swap_tp5_sl2.csv --tag ml_opt_swap_tp5
```

明细报告：

- `analysis/p2b_ml_opt_yolo_report.md`
- `analysis/p2b_ml_opt_rules_expanded_report.md`
- `analysis/p2b_ml_opt_swap_tp5_report.md`
- JSON：`analysis/output/ml_opt_*_sweep.json`

## 扫描了什么（单变量相对 baseline）

| 变体 | 改动 |
|---|---|
| `baseline` | 现网 `LGB_PARAMS` 二分类 |
| `strong_reg` | 更强正则（leaves=7, min_child=50, l2=5） |
| `deeper` | 更深树（leaves=31, lr=0.03） |
| `scale_pos_weight` | 类别不平衡权重 |
| `recency_weight_60d` | 近因样本加权（60d 半衰期） |
| `abs_ret_weight` | 样本权 ∝ \|realized_ret\| |
| **`reg_realized_ret`** | **目标从 label 改为回归 `realized_ret`，按预测收益排序** |
| `seed_ensemble_3` | 3 种子平均 |
| `top15_features` | 只留 baseline gain 前 15 维 |
| `slow_lr_0p02` | 更小学习率 + 更多轮 |
| `baseline_logreg_ma_spread` | 单特征 logreg 对照 |

未扫（有意不做）：ViT/CNN 视觉分类（与 YOLO 检测层职责重叠、重依赖）、XGBoost（本机无包且与 LGB 同族）、消耗 holdout。

## 跨池结果（top 净@0.2%）

| 池 | n val / top-n | baseline top 净 | **最优** | 最优净 | Δ |
|---|---:|---:|---|---:|---:|
| **YOLO 主线** `judgment_yolo_swap` | 349 / 34 | +0.02441 | **reg_realized_ret** | **+0.02923** | **+0.00482** |
| 规则 expanded | 1590 / 159 | +0.00101 | **reg_realized_ret** | **+0.00306** | **+0.00205** |
| 纯 SWAP TP5/SL2 | 1510 / 151 | **−0.00114** | **reg_realized_ret** | **+0.00304** | **+0.00418** |

三池 **同一变体夺冠**，且 SWAP 池把 top-decile 从「扣费后亏」翻成「扣费后赚」。

### 陪跑结论（省流）

| 方向 | 结论 |
|---|---|
| 加深/放慢树 | 偶有小幅波动，**不稳**、跨池不占优 |
| 类别权重 / 近因权重 | **伤净收益** 居多 |
| 特征裁到 top15 | **掉点** |
| 多种子 ensemble | AUC 略升，**经济指标不升** |
| 单特征 logreg | 全面败给 LGB |
| **回归 realized_ret** | **唯一跨池稳定赢家** |

## 解读

1. **标签是瓶颈，不是树深**  
   二分类 `label=1 if TP else 0` 把「大赚 / 小赚 / 小亏 / 大亏」压成 0/1。  
   回归直接对齐 **可交易的收益排序**，与项目成功标准（top-decile 净收益）同构。

2. **AUC 可以骗人**  
   YOLO 池 baseline AUC 已 0.82，回归 AUC 略低（0.816）但 top 净更高。  
   再次印证：本项目不以 AUC 为成功标准。

3. **超参军备竞赛 ROI 低**  
   leaves/lr/ensemble/正则在默认附近抖动；真正阶跃来自 **目标函数语义**。

4. **小样本警示仍在**  
   YOLO top-n=34，Δ 含噪声；但 expanded（top-n=159）与 SWAP（151）同向，降低「纯运气」概率。

## 风险与诚实声明

- val 已被多次选型，**禁止**把本表当样本外绩效宣称。
- 未评估 holdout；若升级主线需 owner 批准冻结 + 前向影子，**不要**为验证本结论再烧 holdout。
- 回归分数不再是概率：现网 `val_q90` 阈值语义变为「预测收益 top 10%」，与排序闸门仍兼容，但 **日志/解释文案要改**。
- 未改 ACTIVE / 前向；发现级结果 ≠ 已上线。

## 建议的 ML 层路线图（优先级）

| 优先级 | 动作 | 谁拍板 |
|---|---|---|
| **P0** | 将 `reg_realized_ret` 做成 **影子配置**（另 tag 冻结，不替换 ACTIVE），前向双账本对比 | **owner** |
| P1 | 在 `train.py` 增加 `--objective {binary,regression}`（默认 binary 保兼容） | 可实现，切默认需 owner |
| P2 | 可选：quantile regression（如 α=0.7）或 pairwise ranking（lambdarank），仍单变量 | 实验 |
| P3 | 不值得：换 XGB/CatBoost、ViT 图像分类、深度时序端到端（成本高、与两层架构冲突） | 暂缓 |
| 检测侧 | YOLO 重标/重训抬候选质量 — 比 ML 拧螺丝杠杆大 | 进行中 |

## 下一步选项

1. **（推荐）** owner 批准后：用 regression 在 `judgment_yolo_swap` 打影子冻结 + 前向并行记分，100 笔后再比 ACTIVE。  
2. 只改代码入口加 flag，默认仍 binary，等前向证据。  
3. 停止 ML 超参扫描，资源回检测层与前向密度。  
4. **不要** 因本结果消耗 holdout。

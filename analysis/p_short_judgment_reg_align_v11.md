# 纠偏：short 判断层对齐 v11 回归主链

**日期**：2026-07-24  
**性质**：Owner 纠正——从「binary + 5 币拧 feat_mirror」改回 **YOLO → 回归 LGBM（预测空头 realized_ret）→ 分位数筛单**；**非**晋升、**非** holdout、**未**改 TP/SL/成本。  
**检测器**：`runs/detect/runs/detect/owner_side_short_tip_v1b/weights/best.pt`（未 promote）  
**池**：`data/judgment_yolo_owner_side_short_30_6m.csv`（30 流动性币 × 6m，镜像特征已在主路径写出）  
**训练 tag**：`p2b_yolo_short_30_6m_reg`（`--objective regression --side short`，无 `--eval-holdout`）  
**指标**：`analysis/output/p2b_yolo_short_30_6m_reg_metrics.json`  
**ACTIVE 对照哲学**：`models/ACTIVE` → `frozen_tp5_sl2_swap_yolo_v11_reg_20260718`（regression / val-q90 / 候选上万）

## 一句话结论

**是的，之前偏了。** short 试点曾落到 binary 小样本 + 把镜像当胜负实验；现已改回与 v11 同构的回归主链。本轮 30×6m 回归：val top-decile 净 **+0.371%**（n=150，扣 0.2%）、Spearman **0.149**、val-q90=**0.00362**——方向对，但池仍远小于 v11（~7.5k vs ~26.7k），**不能**当确认级或晋升依据。

## 1. 纠偏声明

| 曾做错的 | 正确目标（Owner） |
|---|---|
| 默认 binary 训 short，主推 AUC | **regression** 预测 `realized_ret`，主推 top 分位净收益 |
| 5 币 n≈1240，top-decile n=24 极脆 | 扩样本（本轮 30×6m）；哲学对齐 v11「候选上万」 |
| 把 `feat_mirror` 当单变量优化叙事 | 镜像特征 = **主路径默认正确输入（修债）**，不是胜负实验 |
| CLI 无 `--objective`，易默認 binary | 已补 `--objective {binary,regression}`（默认 binary 保兼容；short 主线显式传 regression） |

## 2. 复现命令

```bash
# 30 币名单 + chunked 扫（本机已跑完；resume 安全）
# LaunchAgent: com.fable.yolo_short_30_6m
# OUT=data/judgment_yolo_owner_side_short_30_6m.csv
# SYMBOLS_FILE=analysis/output/yolo_short_30_6m_symbols.txt
# MONTHS=6 END_BEFORE=2026-05-04
# WEIGHTS=runs/detect/runs/detect/owner_side_short_tip_v1b/weights/best.pt

# 回归训练（禁止 --eval-holdout；禁止 promote）
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=. python3 -m src.judgment.train \
  --data data/judgment_yolo_owner_side_short_30_6m.csv \
  --tag p2b_yolo_short_30_6m_reg \
  --side short \
  --objective regression
```

镜像：`scripts/yolo_candidate_source.py --side short` 经 `extract_feature_rows_for_side` → `align_short_feature_rows`；本 CSV 即主路径写出，**无需**再跑 `remap_yolo_short_features.py` / feat_mirror 旁路。

## 3. 数据统计

| 项 | 值 |
|---|---|
| 权重 | tip_v1b `best.pt` |
| side | short only |
| 信号窗 | `[2025-11-04, 2026-05-04)` |
| holdout 泄漏 | **0**（max signal_time = 2026-05-03 17:30） |
| 候选总数 | **7519**（30 币） |
| 正类率 | **0.288** |
| 特征 | 28 列；short 方向镜像已写入 |

时间切分（无 holdout 评估）：

| split | n | range |
|---|---:|---|
| train | 5973 | 2025-11-04 → 2026-03-26 |
| val | 1500 | 2026-03-27 → 2026-05-03 |
| holdout | 0 | 窗内无 ≥2026-05-04 |

## 4. 结果表（回归口径；对照发现级 binary）

主成功标准：预测收益排序 → top 分位扣 **0.2%** 往返后净收益；AUC 仅参考。

| 指标 | 本轮 **reg 30×6m** | 对照 binary 5×6m | 对照 binary feat_mirror 5×6m | 参考 ACTIVE v11_reg（long 池） |
|---|---:|---:|---:|---:|
| objective | **regression** | binary | binary | regression |
| 候选 n | **7519** | 1240 | 1240 | ~26653 |
| val n | **1500** | 248 | 248 | 4120 |
| Spearman(score, ret) | **0.149** | — | — | ρ_mean≈0.61（walkforward） |
| val-q90（预测收益） | **0.00362** | — | — | 0.02022 |
| top-decile 毛收益 | **+0.571%** | +0.262% | +0.356% | （见 v11 freeze 折） |
| top-decile 净（−0.2%） | **+0.371%** | +0.062% | +0.156% | 折净多在 +3–5% 量级 |
| top-decile n | **150** | 24 | 24 | 折 val≈2470 级 |
| top-decile 胜率（label） | 0.36 | 0.375 | 0.375 | ~0.75–0.83 |
| 置换 p（AUC 诊断） | **0.001** | 0.009 | 0.014 | — |
| val AUC（次要） | 0.563 | 0.599 | 0.590 | — |
| best_iteration | 14 | 5 | （见对应 metrics） | 61 |

单特征基线（ma_spread logreg，仍按 label）：top-decile 净 **−0.220%**（n=150）——模型排序仍明显好于该基线。

Gain top5：`atr_pct` / `ret_12` / `pre_range168` / `vol_ratio_mean8` / `close_vs_ema200`。

## 5. 解读

- **扩样本 + 回归目标**后，经济主指标（top-decile 净）相对 5×6m binary 明显抬升，且 n=150 比 n=24 少脆一个数量级——这是纠偏本身带来的可读性，不是「又拧了一下 feat_mirror」。
- Spearman 0.15 << v11 的 ~0.61：排序信号弱很多；阈值 val-q90 也低一个数量级（0.0036 vs 0.020）。诚实解读：short tip 池 + 6m 窗 + 30 币仍远未达到 long v11 全宇宙回归的稳定性。
- AUC 0.56 低于 5 币 binary 的 0.60——**不要**用 AUC 回退叙事；回归主链不以 AUC 决胜。
- 镜像已是默认输入；后续实验若再比「mirror vs 不 mirror」，属于回归债验证，不是主线优化旋钮。

## 6. 风险与诚实声明

- **未** promote / **未**写 ACTIVE / **未**动 holdout / **未**改 TP/SL/成本。
- 30×6m ≈7.5k 仍 **小于** v11 全宇宙 ~26k；发现级，不是确认级。
- 置换检验仍绑在 label-AUC 上（train.py 历史诊断）；经济裁决以 top 分位净收益为准。
- tip_v1b 检测器未过晋升门（tip-smoke 19/27 仅辅证）；判断层数字不洗白检测器。
- best_iteration=14 偏早停；小树 + 短窗可能过拟合局部波动结构。

## 7. 下一步（需 Owner）

1. **建议默认**：同构回归下继续扩宇宙 / 加长窗，逼近 v11 样本量级后再谈 walkforward 与是否申请 freeze（仍不自动 promote）。  
2. 可选：对 short 回归补 5-fold walkforward（对齐 `freeze_model.run_walkforward`），仍无本池、无 holdout。  
3. **停止** feat_mirror 优化叙事；障碍扫参 / holdout 须另批。

## 产物

| 路径 | 用途 |
|---|---|
| `data/judgment_yolo_owner_side_short_30_6m.csv` | 30×6m short YOLO 池（镜像主路径） |
| `analysis/output/p2b_yolo_short_30_6m_reg_metrics.json` | 回归指标 |
| `analysis/output/p2b_yolo_short_30_6m_reg_feature_importance.csv` | gain |
| `analysis/output/p2b_yolo_short_30_6m_reg_train.log` | 训日志 |
| `src/judgment/train.py` | 新增 `--objective` + 回归 val-q90/Spearman |

# short 100×6m 回归单切（发现级，未 holdout / 未 promote）

**日期**：2026-07-24  
**性质**：Owner 批 S2 扩样后，对 `tip_v1b` 扫出的 100 流动性币 × 6m 池做与 30×6m **同构**回归训练。  
**检测器**：`runs/detect/runs/detect/owner_side_short_tip_v1b/weights/best.pt`（未 promote）  
**池**：`data/judgment_yolo_owner_side_short_100_6m.csv`  
**训练 tag**：`p2b_yolo_short_100_6m_reg`（`--objective regression --side short`，**无** `--eval-holdout`）  
**指标**：`analysis/output/p2b_yolo_short_100_6m_reg_metrics.json`  
**walkforward**：见 `analysis/p_short_judgment_100_6m_reg_walkforward.md`

## 一句话结论

扩到 **n=25602**（接近 v11 候选量级哲学）后，单切 top-decile 净仍 **+0.471%**（n=510），略好于 30×6m 的 +0.371%；但 **Spearman 从 0.149 塌到 0.016**，置换 p 从 0.001 松到 **0.037**，val AUC≈0.52——排序信息几乎消失。结合 walkforward（net_mean 仍略正、ρ_mean 为负），本池 = **厚度够了的间歇/弱边，不是稳健可部署边**。按 S2 决策树：**停止继续靠扩币叙事**，回到检测金标 / 信号定义（S3），**不** promote。

## 1. 复现命令

```bash
# 扫池（已完成 100/100）
# scripts/run_yolo_short_100_6m.sh
# OUT=data/judgment_yolo_owner_side_short_100_6m.csv
# WEIGHTS=runs/detect/runs/detect/owner_side_short_tip_v1b/weights/best.pt
# window=[2025-11-04,2026-05-04)

OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=. .venv/bin/python -m src.judgment.train \
  --data data/judgment_yolo_owner_side_short_100_6m.csv \
  --tag p2b_yolo_short_100_6m_reg \
  --side short \
  --objective regression
# 禁止 --eval-holdout；禁止 promote / 写 ACTIVE
```

## 2. 数据统计

| 项 | 值 |
|---|---|
| side | short only |
| 币数 | **100**（名单 `analysis/output/yolo_short_100_6m_symbols.txt`） |
| 候选 n | **25602** |
| 正类率 | **0.284** |
| 信号窗 | 2025-11-04 → 2026-05-03（holdout 行 **0**） |
| 特征 | 28 列；short 镜像主路径已写入 |
| 扫池完成 | 2026-07-24 17:38 UTC+8；complete 100/100 |

时间切分（无 holdout 评估）：

| split | n | range |
|---|---:|---|
| train | 20255 | 2025-11-04 → 2026-03-25 |
| val | 5107 | 2026-03-26 → 2026-05-03 |
| holdout | 0 | 窗内无 ≥2026-05-04 |

## 3. 结果表（与 30×6m 回归对照）

主成功标准：top-decile 扣 **0.2%** 往返净收益；AUC / 置换仅诊断。

| 指标 | **100×6m reg** | 30×6m reg | 读法 |
|---|---:|---:|---|
| 候选 n | **25602** | 7519 | 扩样成功 |
| val n | **5107** | 1500 | 更厚 |
| Spearman(score, ret) | **0.016** | **0.149** | **排序塌陷** |
| val-q90 | **0.00347** | 0.00362 | 阈值仍低一档 |
| top-decile 毛 | **+0.671%** | +0.571% | |
| top-decile 净（−0.2%） | **+0.471%** | +0.371% | 单切仍正、略升 |
| top-decile n | **510** | 150 | 厚度改善 |
| top-decile 胜率（label） | 0.345 | 0.360 | |
| 置换 p（AUC 诊断） | **0.037** | 0.001 | 不再过 0.01 门 |
| val AUC（次要） | 0.516 | 0.563 | 近随机 |
| best_iteration | 11 | 14 | 仍偏早停 |

单特征基线（ma_spread logreg）：top-decile 净 **−0.229%**（n=510）——模型仍好于该基线，但优势主要来自「别选最差」，不是强排序。

Gain top5：`atr_pct` / `pre_range168` / `slow_slope_12` / `close_vs_ema200` / `pre_range48`（波动/位置特征主导，与 30 池同类）。

## 4. 解读

1. **扩币达到了样本量目标**（~2.56 万 ≈ v11 量级哲学），单切 top-decile n 从 150→510，经济点估计仍为正。  
2. **排序质量没有随 n 上升**：Spearman 近 0、AUC≈0.5、置换不过线 → 单切正净更像「弱过滤 + 分位抽样运气/结构」，不是稳定 alpha 排序器。  
3. 与 30 池对照：**不能**说「更大宇宙把边做实了」；更准确是「宇宙放大后，原先 30 池上可见的弱排序被稀释/摊平」。  
4. best_iteration=11 与 walkforward 多折 best_iteration=1 一致：模型几乎学不到可迁移结构。

## 5. 风险与诚实声明

- **未** promote / **未**写 ACTIVE / **未**动 holdout / **未**改 TP/SL/成本。  
- 100 币名单仍是主观流动性宇宙，非成交量严格排序。  
- tip_v1b 未过检测晋升门；本表不洗白检测器。  
- 确认级仍只认前向新鲜 100 笔。  
- 单切正净 **不足以** 申请 freeze。

## 6. S2 决策树裁决

| 分支 | 是否命中 |
|---|---|
| 稳住（单切正 + walkforward 明显好于 30） | **否**（ρ 更差；net_mean 未改善） |
| 仍间歇 | **是** |
| 转负/随机 | 净未转负，但排序近随机 |

→ **停止继续扩样本叙事**；默认进入 **S3 检测金标加固 / 信号定义复核**。障碍/holdout/promote **不**开。

## 7. 产物

| 路径 | 用途 |
|---|---|
| `data/judgment_yolo_owner_side_short_100_6m.csv` | 100×6m short YOLO 池 |
| `analysis/output/p2b_yolo_short_100_6m_reg_metrics.json` | 单切指标 |
| `analysis/output/p2b_yolo_short_100_6m_reg_feature_importance.csv` | gain |
| `analysis/output/p2b_yolo_short_100_6m_reg_train.log` | 训日志 |
| `analysis/p_short_judgment_100_6m_reg_walkforward.md` | walkforward |

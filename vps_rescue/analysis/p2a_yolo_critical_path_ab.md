# A/B: YOLO候选源 vs 规则候选源（SWAP，发现级 val-only）

**日期**：2026-07-15  
**纪律**：未加 `--eval-holdout`；未改冻结模型 / `forward_log` / `features.py`。  
**设定**：同一判断层（LightGBM + 28 维特征）、同一 train/val 时间切分与 purge；**仅候选源不同**。

## 复现命令

```bash
# 1) YOLO 候选（已完成产物 data/judgment_yolo_swap.csv，2385 行）
PYTHONPATH=. .venv/bin/python scripts/yolo_candidate_source.py \
  --weights models/owner_best.pt --out data/judgment_yolo_swap.csv

# 2) 判断层对照（本报告补跑）
python3 -m src.judgment.train --data data/judgment_yolo_swap.csv --tag ab_yolo
python3 -m src.judgment.train --data data/swap_replication/swap_tp5_sl2.csv --tag ab_rules
```

## 数据统计

| 路径 | 文件 | 行数 | train | val | val 正类率 |
|---|---|---:|---:|---:|---:|
| YOLO 候选 | `judgment_yolo_swap.csv` | 2385 | 1382 | 349 | 42.12% |
| 规则候选 | `swap_replication/swap_tp5_sl2.csv` | （既有） | 6027 | 1510 | 32.12% |

YOLO 池：扫描完成时日志约 101 币 / 2385 候选；holdout 段样本存在但本报告 **未评估 holdout**。

## val 结果

| 配置 | AUC | p | top-n | top gross | 净@0.2% | 净@maker0.06% | top 胜率 |
|---|---:|---:|---:|---:|---:|---:|---:|
| **YOLO候选** | 0.8172 | 0.001 | 34 | +0.02641 | +0.02441 | +0.02581 | 85.3% |
| **规则候选** | 0.5601 | 0.001 | 151 | +0.00086 | -0.00114 | +0.00026 | 32.5% |

## 判定

门槛（`docs/design/yolo_critical_path_ab.md`）：YOLO top-decile **净** ≥ 规则 **且** p&lt;0.01。

| 门槛 | 结果 |
|---|---|
| 净@0.2%：+0.02441 ≥ -0.00114 | ✓ |
| p=0.001 &lt; 0.01 | ✓ |

**形式判定：发现级通过**  
（净@maker 同步：YOLO +0.02581 vs 规则 +0.00026）

## 解读与异常警示（必读）

1. **YOLO 数字好得反常**：val AUC **0.8172**、top 净 **+2.44%**/笔、胜率 **85%**。按项目纪律，**第一假设是小样本或选择偏置，不是立刻上线**。
2. **top-n 仅 34 笔**（val 349 的 10%），经济指标方差极大；规则 top-n=151 更稳。
3. **池不可比**：YOLO 是检测器挑中的「像密集」的 bar；规则是 expanded 扫描全覆盖。YOLO 可能自带更易赚钱的形态过滤（选择偏置），AUC 高 ≠ 检测层可替换规则。
4. 特征–标签相关：`order_score` 等在 YOLO 池上相关偏高，符合「视觉上更整齐的密集」子集，仍属因果特征，但 **子集效应** 会抬升可分性。
5. 扫描中途脚本曾被改写导致 step2 中断；本报告为 **同一 CSV 上补跑 train**，候选定义未变。

## 风险与诚实声明

- 未评估 holdout；未改前向主线；未把 YOLO 候选源写入生产冻结配置。
- 形式通过 **不等于** 允许切换关键路径：设计要求再 **冻结独立配置 + 前向影子** 才可升级。
- 规则侧净@0.2% 为负（-0.00114）与历史 SWAP 复制报告口径可能因成本假设（0.2% vs maker 0.06%）不同；同表对照仍以本 run 为准。

## 下一步（需 owner）

1. **默认**：记录「发现级线索」，**不**替换规则扫描主线；继续 round6 打标提检测召回/精度。  
2. * 若要升级关键路径：单独冻结 YOLO 候选流水线 + 前向双账本，满样本后再比一次。  
3. 复现/加压：扩大 YOLO 候选（降 conf / 增币种）后再 A/B，看 top-n≥100 时净是否仍 ≥ 规则。

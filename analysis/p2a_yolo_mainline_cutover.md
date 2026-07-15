# YOLO 主线切换（owner 2026-07-15）

## 决策

owner 指示：**先把 YOLO 候选源切进主线**；round6 新标训出更好检测器后再换 `owner_best` 权重。  
发现级 A/B 已形式过线（见 `analysis/p2a_yolo_critical_path_ab.md`），同步接受小样本/选择偏置风险。

## 切换内容

| 组件 | 旧（规则主线） | 新（YOLO 主线） |
|---|---|---|
| 候选源 | expanded 规则 `strict_mask` | `owner_best` 滑窗检测框 → 信号 bar |
| 判断模型 | `frozen_tp5_sl2_swap_20260709` | `frozen_tp5_sl2_swap_yolo_20260715` |
| 训练池 | `data/swap_replication/swap_tp5_sl2.csv` | `data/judgment_yolo_swap.csv` |
| val q90 阈值 | 0.3747 | **0.7109** |
| 前向起点 | 2026-07-08 | **2026-07-15**（新时钟） |
| 前向日志 | `data/forward_log.csv` | 清空后重建；旧日志归档 |

## 归档

- 规则时代前向：`data/forward_log_rules_pre_yolo_20260715.csv`
- 规则冻结工件仍在：`models/frozen_tp5_sl2_swap_20260709.*`（rollback：`freeze_model --legacy-rules` + `CANDIDATE_SOURCE=rules`）

## 代码入口

- `src/judgment/forward_types.py`：`CANDIDATE_SOURCE = "yolo"`，`FORWARD_START = 2026-07-15`
- `src/judgment/yolo_candidates.py`：扫描实现
- `src/judgment/forward_scan.py`：主线/H1 影子共用 YOLO 候选
- `src/judgment/frozen.py`：`DEFAULT_CONFIG_NAME = tp5_sl2_swap_yolo`
- `models/ACTIVE` → `models/frozen_tp5_sl2_swap_yolo_20260715.txt`

## 运维

```bash
# 冻结（已执行）
PYTHONPATH=. python3 scripts/freeze_model.py --date 20260715 --write-active

# 前向（需 .venv：含 ultralytics）
PYTHONPATH=. .venv/bin/python scripts/forward_track.py

# 回滚候选源：改 forward_types.CANDIDATE_SOURCE="rules" 并 ACTIVE 指回规则冻结
```

## 风险

- YOLO val top-n 小、AUC 偏高 → 实盘/前向可能回归；用新前向时钟重新积累。
- 前向每次全库 YOLO 推理成本高，务必用 `.venv` 且避免与重训并行。
- 检测权重仍是 v6_chain；round6 标完后只替换 `models/owner_best.pt` 再重扫/可选重冻判断层。

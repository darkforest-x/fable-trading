# 判断层切 ACTIVE：YOLO + 回归 realized_ret

**日期**：2026-07-15  
**owner 授权**：对话中要求「影子配置并且换 ACTIVE」+ 看板对照。

## 做了什么

| 项 | 值 |
|---|---|
| ACTIVE | `models/frozen_tp5_sl2_swap_yolo_reg_20260715.txt` |
| config | `tp5_sl2_swap_yolo_reg` |
| objective | **regression**（预测 `realized_ret`） |
| 数据集 | `data/judgment_yolo_swap.csv` |
| 阈值 val q90 | **0.01654**（预测收益，不是概率） |
| SHADOW 二分类 | `models/frozen_tp5_sl2_swap_yolo_20260715.txt`（`models/SHADOW_BINARY_YOLO`） |
| ACTIVE_PREV | 切流前 ACTIVE 备份 |

## 复现

```bash
PYTHONPATH=. python3 scripts/freeze_model.py --write-active
# score cache 由 freeze 后重建；看板读 scored_signals_swap*
```

## 回测（阶段 3，成本 0.3%）

| | ACTIVE 回归 | SHADOW 二分类 |
|---|---:|---:|
| 合格信号 | 381 | 272 |
| 验收笔数 | **102** | 49 |
| 验收净/资金 | **+32.4%** | +10.3% |
| 验收 PF | 6.86 | 7.88 |
| 验收胜率 | 79.4% | 81.6% |
| 笔数≥100 | **✓** | ✗ |
| 全期笔数 | 380 | 272 |
| 全期净/资金 | +131% | +79% |

产物：`analysis/output/p3_yolo_reg_backtest.json`、`p3_ml_opt_backtest_compare.json`  
看板：Backtest 页「判断层对照」表 + `/api/backtest/compare`

## 回滚

```bash
# 指回二分类 YOLO
echo 'models/frozen_tp5_sl2_swap_yolo_20260715.txt' > models/ACTIVE
# 或
PYTHONPATH=. python3 scripts/freeze_model.py --binary-yolo --write-active
# 然后重建 score cache / 重启 dashboard
```

## 风险

- 验收窗 = 已披露 holdout 时间窗，**不是新的正式验收**。
- 分数语义变了：前向/日志里 score 是预测收益，阈值 ~0.017。
- YOLO 池选择偏置仍在；前向 100 笔才是硬闸门。

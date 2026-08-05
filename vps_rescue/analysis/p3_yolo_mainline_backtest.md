# YOLO 主线整体回测（切流后，2026-07-15）

**配置**：候选=`owner_best` YOLO · 判断=`frozen_tp5_sl2_swap_yolo_20260715` · 出场 TP5/SL2  
**数据**：`data/judgment_yolo_swap.csv`（2385 候选）  
**纪律**：未加 `--eval-holdout`；验收窗 ≥2026-05-04 已披露为 2b 曾消耗窗（数字仅对照）。

## 复现

```bash
PYTHONPATH=. python3 -m src.backtest.run --data data/judgment_yolo_swap.csv --tag p3_yolo
PYTHONPATH=. python3 -m src.backtest.maker_val_sim --data data/judgment_yolo_swap.csv \
  --out analysis/output/p3_yolo_maker_val_sim.json
```

冻结阈值 val q90 = **0.71087**  
eligible（score≥阈值）= **272** / 2385

## 组合回测（10 仓帽、同币锁仓）

### 验收窗 ≥2026-05-04（披露：已消耗 holdout 窗，不作正式验收宣称）

| 往返成本 | 笔数 | 净/资金 | 净/笔 | 胜率 | PF | maxDD |
|---|---:|---:|---:|---:|---:|---:|
| 0.20% | 49 | +10.77% | +0.02198 | 81.6% | 8.665 | 0.42% |
| 0.30%（基线） | 49 | +10.28% | +0.02098 | 81.6% | 7.876 | 0.43% |

验收勾选（基线 0.30%）：  
- 净>0：True  
- PF≥1.3：True  
- maxDD≤20%：True  
- **笔数≥100：False**（本窗仅 49 笔 → **样本不足**）

### 全时段 / 窗前（in-sample 参考）

| 窗口 | 笔数 | 净/资金 | 净/笔 | 胜率 | PF | maxDD |
|---|---:|---:|---:|---:|---:|---:|
| 窗前 in-sample @0.3% | 223 | +69.14% | +0.03100 | 96.9% | 98.887 | 0.15% |
| 全时段 @0.3% | 272 | +79.42% | +0.02920 | 94.1% | 37.08 | 0.26% |

## Val 窗 maker 组合（SWAP 费率）

阈值 0.71087 · val 2026-03-12→2026-05-03  
maker 成本 0.0006 · **maker_filled 假定全成**（`maker_filled_assumed=True`）

| 池 | 笔数 | 净/笔 | 胜率 | PF | maxDD |
|---|---:|---:|---:|---:|---:|
| maker 全量 | 35 | +0.02571 | 88.6% | 41.42 | 0.11% |
| +H9 站上均线 | 22 | +0.03028 | 90.9% | 109.104 | 0.05% |
| taker 全量 | 35 | +0.02531 | 88.6% | 38.123 | 0.11% |

## 解读

1. 经济数字很好看（高胜率/高 PF），但 **验收窗笔数远低于 100**，且 YOLO 池本身是检测器预筛过的子集 → **选择偏置 + 小样本**，不能当实盘保证。
2. 全时段 272 笔仍偏少；前向新时钟从 2026-07-15 起重新积累才是切主线后的「真验证」。
3. 看板已用新冻结阈值 **0.71087** 重建 `scored_signals_swap`（eligible 272）。

## 风险与诚实声明

- 数字好得反常时第一假设是偏置/过拟合，不是 alpha 翻倍。
- holdout 窗仅作对照，不作第 N 次正式验收。
- maker_filled 在 YOLO CSV 中缺失，本报告 maker 路径假设全部成交。

## 前端 / VPS

- 本机：`models/ACTIVE` + `scored_signals_swap*` 已指向 YOLO 冻结。
- 部署：`bash scripts/deploy_vps.sh`（含 judgment_yolo 数据与新 models）。

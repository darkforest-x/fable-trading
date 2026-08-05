# P1.5 R1'：H9 高层趋势过滤复测与推广

**日期**：2026-07-09
**纪律**：发现级 val-only；未读取 holdout，未触碰 2026-05-04 之后验收窗口；未改阈值、障碍参数或成本假设。
**val 记账**：本轮新增 5 个 val 输出（含 1 个现货 H9 top-bucket 复现）：spot top-bucket、SWAP top-bucket、spot maker 组合、SWAP maker 组合、SWAP 特征版重训。

## 复现命令

```bash
PYTHONPATH=. python3 scripts/h9_trend_filter.py \
  --data data/sweep_v3/judgment_v3_tp5_sl2_h72.csv \
  --out analysis/output/h9_spot_trend_filter.json

PYTHONPATH=. python3 scripts/h9_trend_filter.py \
  --data data/swap_replication/swap_tp5_sl2.csv \
  --out analysis/output/h9_swap_trend_filter.json

PYTHONPATH=. python3 -m src.backtest.maker_val_sim \
  --data data/sweep_v3/judgment_v3_tp5_sl2_h72.csv \
  --out analysis/output/p3_spot_h9_maker_val_sim.json

PYTHONPATH=. python3 -m src.backtest.maker_val_sim \
  --data data/swap_replication/swap_tp5_sl2.csv \
  --out analysis/output/p3_swap_h9_maker_val_sim.json

PYTHONPATH=. python3 scripts/h9_feature_retrain.py \
  --data data/swap_replication/swap_tp5_sl2.csv \
  --out analysis/output/h9_swap_feature_retrain.json
```

## 数据统计

| 池 | 数据集 | val 时间 | H9 above 通过率 | H9 slope 通过率 |
|---|---|---|---:|---:|
| spot expanded | `data/sweep_v3/judgment_v3_tp5_sl2_h72.csv` | 2026-03-25 ~ 2026-05-03 | 35.9% | 27.7% |
| SWAP mainline | `data/swap_replication/swap_tp5_sl2.csv` | 2026-03-17 ~ 2026-05-03 | 34.1% | 27.9% |

## top-bucket 过滤复测

| 池 | 过滤 | n | 净@maker/笔 | 胜率 |
|---|---|---:|---:|---:|
| spot | 无过滤 | 160 | +0.152% | 48.1% |
| spot | `h1_above_ma` | 82 | **+0.203%** | **51.2%** |
| spot | 逆势 | 59 | +0.072% | 44.1% |
| SWAP | 无过滤 | 151 | +0.026% | 35.8% |
| SWAP | `h1_above_ma` | 58 | +0.066% | 39.7% |
| SWAP | `h1_up_slope` | 52 | **+0.073%** | 40.4% |
| SWAP | 逆势 | 79 | -0.014% | 32.9% |

**解读**：H9 在 SWAP 池方向复现：顺 1h 趋势的 top-bucket 净值与胜率都高于无过滤，逆势组为负。与 spot 不同，SWAP 的 `up_slope` 略优于 `above_ma`，但差距小且样本只有 52/58。

## maker 组合模拟

| 池 | 过滤 | 笔数 | PF | 净收益/资金 | 净/笔 | 胜率 | maxDD |
|---|---|---:|---:|---:|---:|---:|---:|
| spot | 无过滤 | 124 | 1.271 | +1.30% | +0.105% | 44.4% | 0.70% |
| spot | `h1_above_ma` | 65 | **1.520** | +1.18% | **+0.182%** | 49.2% | 0.55% |
| SWAP | 无过滤 | 123 | 0.964 | -0.19% | -0.015% | 33.3% | 1.56% |
| SWAP | `h1_above_ma` | 49 | 1.204 | +0.36% | +0.073% | 38.8% | 0.73% |
| SWAP | `h1_up_slope` | 44 | **1.281** | +0.44% | **+0.100%** | 40.9% | 0.53% |

**判定**：H9 过滤显著改善组合经济性，但 SWAP 主线仍未越过 PF 1.3。`h1_up_slope` 在 SWAP 组合模拟接近通过线（PF 1.281），但交易数只有 44，不能据此切主线。

## 特征版重训（SWAP）

只新增单一特征 `h1_above_ma`，不打包 `up_slope`，保持单变量纪律。

| 模型 | val AUC | p | top 净@maker | top 胜率 | best_iter |
|---|---:|---:|---:|---:|---:|
| baseline 28特征 | 0.560 | 0.001 | +0.026% | 32.5% | 25 |
| + `h1_above_ma` | 0.553 | 0.001 | **+0.180%** | **39.7%** | 18 |

`h1_above_ma` 在特征重要性中 gain 排第 27（gain 24.43，split 2）。模型没有把它当成主特征，但加进去后 top-decile 经济性明显改善；AUC 略降，说明它更像收益排序的局部过滤器，不是全面提升分类判别力的强特征。

## 风险与诚实声明

- 本轮全部是发现级 val 复测；val 已经被反复看过，数字只能用于排序线索；
- SWAP 组合过滤后样本只有 44-49 笔，PF 接近 1.3 但证据不足；
- H9 不能替代前向确认：任何主线配置升级仍需冻结配置后的前向日志；
- 资金费、盘口排队成交仍是简化口径，前向阶段继续记录真实可成交性。

## 下一步

H9 记录为“SWAP 方向复现但未确认通过”。主线不切换；按 `NEXT_STEPS.md` 进入 R2/H10 做空侧。

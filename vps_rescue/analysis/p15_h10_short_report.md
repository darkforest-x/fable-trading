# P1.5 R2：H10 做空侧镜像验证

**日期**：2026-07-09
**纪律**：发现级 val-only；提交版输出只报告 train/val，未评价 holdout；未改多头路径、阈值预设、障碍参数或成本假设。
**val 记账**：本轮新增 1 个 val 输出（SWAP 空头 TP5/SL2 独立池）。

## 复现命令

```bash
PYTHONPATH=. python3 scripts/short_replication.py
python3 -m pytest tests/test_short_side.py -q
```

## 数据统计

脚本扫描 116 个 `*_USDT_SWAP` 15m 序列；候选规则为 expanded 池镜像，标签为 TP5/SL2、horizon=72。

| 数据集 | 样本数 | 时间范围 | 正类率 |
|---|---:|---|---:|
| train | 5,737 | 2025-06-04 22:00 UTC ~ 2026-03-17 15:00 UTC | 32.14% |
| val | 1,445 | 2026-03-18 09:45 UTC ~ 2026-05-03 05:00 UTC | 27.89% |
| total candidates | 8,976 | train/val 加未汇总的后续窗口 | — |

## 镜像设计

候选扫描新增 `scan_short_candidates`，不改 `scan_candidates`。主要镜像关系：

| 多头语义 | 空头语义 |
|---|---|
| `drawdown24` | `runup24` |
| `ext_up = close / cluster_max - 1` | `ext_down = cluster_min / close - 1` |
| `order_score` | `down_order_score` |
| long maker filled: `low(entry_bar) < open` | short maker filled: `high(entry_bar) > open` |
| TP 在 entry 上方 | TP 在 entry 下方 |

训练特征保留 `FEATURE_COLUMNS` 形状，但对空头样本做方向对齐：`ext_up←ext_down`、`order_score←down_order_score`、`drawdown24←runup24`、`close_vs_ema*` 改为 `ema*/close - 1`、近期收益取反。这样模型看到的是“顺空头方向的强弱”，而不是直接复用多头含义。

## 结果表

| 模型 | val AUC | p | top gross | top net@taker0.10% | top net@maker0.06% | top 胜率 | top maker fill | best_iter |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| LightGBM short TP5/SL2 | 0.6174 | 0.001 | +0.265% | +0.165% | +0.205% | 36.81% | 86.1% | 58 |
| ma_spread 单特征 baseline | 0.5711 | — | +0.403% | +0.303% | +0.343% | 40.97% | — | — |

top-decile filled-only：LightGBM `top_net_maker_filled_only = +0.131%`。

## 解读

H10 按 2b 发现级门槛通过：`p=0.001 < 0.01`，且 top-decile `net@maker=+0.205% > 0`。这说明向下密集破位在 SWAP 宇宙里不是纯噪音，空头侧有可继续研究的 alpha 线索。

但 LightGBM 没有击败单特征 `ma_spread` baseline。baseline 的 top-decile 净@maker 为 +0.343%，高于模型的 +0.205%。这通常意味着两种可能：空头侧真正主导因素更接近“密集程度本身”，或当前方向对齐后的 28 特征引入了噪声。结论不能写成“空头模型已优于简单规则”，只能写成“空头侧信号本身发现级成立”。

特征重要性 top5：`ret_12`、`atr_pct_ratio96`、`ext_up`（空头对齐后的 ext_down）、`close_vs_ema55`、`drawdown24`（空头对齐后的 runup24）。模型主要在吃短期反向动量、波动相对状态和下行延展。

## 风险与诚实声明

- 本轮是发现级 val-only；val 已多次用于选型，不能作为最终绩效承诺；
- baseline 强于 LightGBM，说明 H10 暂不能作为主线模型替换项；
- 空头侧 maker filled 仍是 OHLC 粒度近似，真实盘口排队需要前向验证；
- 开发过程第一版脚本曾把 `holdout_unused` split 摘要打印到 stdout；已删除并重跑，提交版 JSON 与本报告不含 holdout 汇总，也不使用该信息做任何判断；
- sklearn logistic baseline 在本机打印了数值 warning，但输出完成；baseline 只作参考，不作为通过门槛。

## 下一步

H10 记录为“发现级通过，但模型价值未超过单特征 baseline”。主线不切换；下一项按 `NEXT_STEPS.md` 进入 R3：H1+H2 出场复合，与 TP5/SL2 基线同表对比。

# P1.5 R3：H1/H2 出场复合验证

**日期**：2026-07-09
**纪律**：发现级 val-only；只用当前 SWAP 主线宇宙；未评价 holdout；未改候选阈值、入场规则或成本假设。
**val 记账**：本轮新增 3 个 val 配置输出（TP5/SL2 baseline、H1 scaled、H2 breakeven）。

## 复现命令

```bash
PYTHONPATH=. python3 scripts/exit_variants_sweep.py
python3 -m pytest tests/test_exit_variants_sweep.py -q
```

## 数据统计

脚本扫描 116 个 `*_USDT_SWAP` 15m 序列；候选规则固定为 expanded，horizon 固定 72。

| 配置 | 候选数 | train | val | val 正类率 |
|---|---:|---:|---:|---:|
| TP5/SL2 baseline | 9,312 | 6,027 | 1,510 | 32.12% |
| H1 scaled：半仓 2.5×ATR + 尾仓 3×ATR 拖尾 | 9,312 | 6,027 | 1,510 | 53.05% |
| H2 breakeven：+1.5×ATR 后 SL=entry | 9,312 | 6,027 | 1,510 | 22.45% |

## top-decile 结果

| 配置 | val AUC | p | top gross | 净@taker0.10% | 净@maker0.06% | top 胜率 | top maker fill |
|---|---:|---:|---:|---:|---:|---:|---:|
| TP5/SL2 baseline | 0.5601 | 0.001 | +0.086% | -0.014% | +0.026% | 32.45% | 84.8% |
| **H1 scaled** | **0.6106** | **0.001** | **+0.386%** | **+0.286%** | **+0.326%** | **72.85%** | 86.1% |
| H2 breakeven | 0.5172 | 0.1738 | +0.156% | +0.056% | +0.096% | 21.85% | 90.7% |

filled-only top-decile 净@maker：TP5/SL2 `+0.001%`，H1 scaled `+0.340%`，H2 breakeven `+0.046%`。

## maker 组合模拟

同一 val 窗口、同一 10 仓帽、同币种锁仓；maker 池只保留 `maker_filled=True` 的信号。

| 配置 | 笔数 | PF | 净收益/资金 | 净/笔 | 胜率 | maxDD |
|---|---:|---:|---:|---:|---:|---:|
| TP5/SL2 baseline | 123 | 0.964 | -0.19% | -0.015% | 33.33% | 1.56% |
| **H1 scaled** | **125** | **2.825** | **+4.15%** | **+0.332%** | **71.20%** | **0.29%** |
| H2 breakeven | 137 | 1.157 | +0.58% | +0.042% | 19.71% | 0.80% |

## 判定

H1 scaled 发现级强通过：相对 TP5/SL2 baseline，top-decile 净@maker 从 `+0.026%` 提升到 `+0.326%`，组合 PF 从 `0.964` 提升到 `2.825`，maxDD 从 `1.56%` 降到 `0.29%`。它不仅满足 H1 的“净@maker 高于 TP5 基线 +0.02%/笔”，也显著改善了组合经济性。

H2 breakeven 单独不通过：AUC 0.5172、p=0.1738，统计显著性不足；虽然 top 净值为正、maxDD 好于 TP5，但模型排序不可靠，不能升级为主线候选。

## 解读

H1 的有效性符合此前“短促脉冲而非持久趋势”的结论：先在 2.5×ATR 锁住半仓利润，再给剩余半仓 3×ATR 拖尾，既减少 TP5 等完整目标的错失，又避免纯拖尾太早被震出。H2 的保本逻辑切掉一部分左尾，但也把不少原本会触及 TP5 的交易提前归零，导致标签正类率和模型可分性下降。

## 风险与诚实声明

- 本轮仍是发现级 val-only；val 已多次用于选型，H1 的最终裁决只能靠前向数据；
- H1 scaled 改变的是标签/出场结构，不等于实盘执行已验证；前向日志需要记录半仓落袋与尾仓拖尾的可执行性；
- 组合模拟仍基于 15m OHLC 的 maker-filled 近似，真实盘口排队可能降低成交质量；
- 本次脚本把原全宇宙 H1/H2 原型升级为 SWAP-only 口径，旧 `analysis/output/exit_variants.json` 保留为历史产物，新结论以 `analysis/output/exit_variants_swap.json` 为准。

## 下一步

H1 scaled 记录为 SWAP 主线的最强发现级候选，但暂不替换冻结前向主线；需要单独冻结/前向确认后才能升级。按 `NEXT_STEPS.md` 进入 R4：H7/H8 多时间框架池。

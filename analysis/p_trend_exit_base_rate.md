# 趋势出场 base rate — 2026-07-23

**纪律**：纯离线，`<2026-05-04`（**未碰 holdout**）；入场全表统一；只改出场；
成本 maker 0.06% + legacy 0.20%。不 promote、不下单、不改 ACTIVE / 三门 / forward_log。
Owner 已批：「策略按趋势理解」「批准改出场」「最终目标是收益」。

**问题**：固定同一因果入场后，把出场从固定障碍改成趋势式，能否让某一边
train **PF@maker ≥ 1.3**，或净收益合计显著转正？

## 裁决句（醒目）

**空边：趋势出场抬过 1.3。** 三套过线——

| 出场 | 边 | n | 净合计@maker | PF@maker | 相对 baseline 空 |
|---|---|---:|---:|---:|---|
| **no_tp_sl2_h144**（无 TP，仅 SL2） | **short** | 6166 | **+21.81** | **1.415** | baseline 1.245 → **过线** |
| **trail3_atr_h144** | **short** | 6166 | +11.26 | **1.339** | 过线 |
| **ma_ema55_h144** | **short** | 6166 | +13.18 | **1.316** | 过线 |

**多边：换出场仍不够**——所有变体 PF@maker ∈ 0.82–0.97，净合计全负。

张力：`time_stop_96` short 净合计 **+21.81**（与 no_tp 几乎并列第一）但 PF **1.244 < 1.3**——
利润厚、尾部亏也厚。结构退出 short PF **1.290** 擦线未过。

## 复现命令

```bash
PYTHONPATH=. .venv/bin/python scripts/trend_exit_base_rate.py \
  --n-symbols 0 --tag trend_exit_base_rate
# 输出: analysis/output/trend_exit_base_rate.json
#       analysis/output/trend_exit_base_rate.csv
```

## 底座与约定（入场全表统一）

| 项 | 值 |
|---|---|
| 入场规则 | **`spread_expand_chg8`**（证据最强：launch/择向分边表空边 PF 1.245） |
| 入场判定 | tip 后首次 `Δfast_spread(8) ≥ 0.00383`；`close ≥ cluster_mid → 多` 否则空 |
| 入场价 | **next_open**（`p_entry_timing_close_vs_next` 尚未有发布结论；默认并注明） |
| 密集 tip | `fast≤0.0028` & `full≤0.0055`，run==5；wait≤48；MIN_GAP=18 |
| 宇宙 | OKX SWAP，剔 stockish / frozen-eval；train only |
| 主表 | **long \| short 分行**；both 仅对照 |
| 优化目标 | 主排序 **净收益合计@maker** 与 **PF@maker**；附胜率 / 均持仓 / MDD 近似 |

Baseline 数字与 [`p_launch_entry_long_short.md`](./p_launch_entry_long_short.md) /
[`p_direction_select_base_rate.md`](./p_direction_select_base_rate.md) 同规则行一致
（long 5800 / short 6202，PF 0.892 / 1.245）——归因干净。

## 出场变体（预声明）

| 变体 | 规则 |
|---|---|
| baseline_tp5_sl2_h72 | 固定 TP5/SL2，horizon **72**（主线对照，必须保留） |
| trail3_atr_h144 | 吊灯跟踪 **3×ATR14(signal)**；无固定 TP；stop 以入场价为种子；timeout **144** |
| ma_ema55_h144 | 收盘反向穿越 **EMA55**（无 ema20，库内最近慢均线选 55）；timeout 144 |
| structure_mid_redense_h144 | 收盘回到 `cluster_mid` 另一侧 **或** `fast_spread≤0.0028`；timeout 144 |
| time_stop_48 / 96 | 持有 N 根收盘强制平；无 TP/SL |
| no_tp_sl2_h144 | **仅 SL2、无 TP**；timeout 144（让利润奔跑） |

趋势类 timeout=144 相对 baseline h72 更长——故意留给趋势跑；时间止损则是独立对照。

## 数据统计

| 项 | 值 |
|---|---|
| 币数 | 233（略少于先前 236：趋势 horizon 需更长尾部，短序列被剔） |
| 时间 | 2025-06-05 → 2026-05-03（train） |
| 入场触发 | 12002（与历史 spread both n 一致量级） |

## 对照表（主裁决 = long / short）

| 出场 | 边 | n | 胜率 | 净合计@m | 均净@m | PF@maker | PF@0.2% | 均持仓 | MDD≈ |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline_tp5_sl2_h72 | long | 5800 | 29.9% | −4.80 | −0.00083 | 0.892 | 0.743 | 20.2 | −4.78 |
| baseline_tp5_sl2_h72 | **short** | 6202 | 35.4% | +10.47 | +0.00169 | **1.245** | 1.037 | 21.5 | −0.35 |
| trail3_atr_h144 | long | 5748 | 34.3% | −5.87 | −0.00102 | 0.839 | 0.667 | 18.5 | −5.85 |
| trail3_atr_h144 | **short** | 6166 | 41.2% | +11.26 | +0.00183 | **1.339** | 1.068 | 19.6 | −0.34 |
| ma_ema55_h144 | long | 5748 | 28.6% | −1.16 | −0.00020 | 0.973 | 0.813 | 31.9 | −2.85 |
| ma_ema55_h144 | **short** | 6166 | 33.9% | +13.18 | +0.00214 | **1.316** | 1.096 | 35.0 | −0.89 |
| structure_mid_redense_h144 | long | 5748 | 27.5% | −5.10 | −0.00089 | 0.859 | 0.689 | 20.7 | −5.27 |
| structure_mid_redense_h144 | short | 6166 | 34.3% | +9.91 | +0.00161 | 1.290 | 1.032 | 23.8 | −0.60 |
| time_stop_48 | long | 5805 | 44.3% | −13.05 | −0.00225 | 0.817 | 0.722 | 48.0 | −13.16 |
| time_stop_48 | short | 6202 | 52.0% | +13.83 | +0.00223 | 1.228 | 1.079 | 48.0 | −1.02 |
| time_stop_96 | long | 5797 | 45.0% | −14.37 | −0.00248 | 0.857 | 0.786 | 96.0 | −14.45 |
| time_stop_96 | short | 6170 | 52.1% | **+21.81** | +0.00353 | **1.244** | 1.141 | 96.0 | −1.14 |
| no_tp_sl2_h144 | long | 5748 | 16.2% | −3.86 | −0.00067 | 0.927 | 0.800 | 45.7 | −4.05 |
| no_tp_sl2_h144 | **short** | 6166 | 20.7% | **+21.81** | +0.00354 | **1.415** | **1.222** | 52.6 | −0.83 |

## 解读

1. **固定 TP 在空边是在砍趋势**：baseline 空 PF 1.245 已接近线；拿掉 TP（`no_tp_sl2`）
   后 PF→**1.415**、净合计翻倍——赢家在跑，输家仍被 SL2 截住。胜率掉到 20.7% 是预期形态
   （趋势：少胜多、大赚小亏）。
2. **吊灯 / EMA55** 也能过 1.3，但净合计与 PF 都低于「仅 SL」。trail 均持仓最短（~20），
   MDD 也最浅——更「交易友好」，代价是少吃尾部。
3. **时间止损**净厚但 PF 不过线：说明 spread-short 底座本身有漂移偏置，靠拖时间吃到钱，
   但亏损尾不够薄 → 不算可部署过线。
4. **多边仍系统性薄**：任何趋势出场都救不了；多边问题不在出场。
5. **扣 0.2%**：仅 `no_tp_sl2` short 仍 >1.2（1.222）；trail/ma 掉回 ~1.07–1.10——
   成本敏感，实盘须认 maker 路径。

## 风险与诚实声明

- 仍是 **train 因果 base rate**，不是 holdout / 前向 100 笔；过 1.3 ≠ 可 promote。
- 趋势 timeout=144 与 baseline h72 不完全同视界——结论是「趋势式相对固定障碍更好」，
  不是「在同一 72 根窗内调参最优」。
- `no_tp_sl2` 胜率 20.7%：实盘心理与回撤路径依赖大赢家；MDD≈−0.83（按笔累计近似）
  浅于多边，但仍是序列近似，非真实组合回撤。
- 入场仍绑 spread 散开跟向；未重扫入场网格（单主题纪律）。
- 未改 ACTIVE / 实盘 / 三门；本报告不构成开空授权。

## 下一步（需 owner 决策）

1. **影子跟踪** `spread_expand + no_tp_sl2`（或 trail3）空边——仅纸面，不自动下单  
2. 是否批准 **holdout 对照一次**（会记第 7 次）验证 train 过线是否虚  
3. 多边另开命题（入场/过滤），不要再指望换出场  
4. 默认：收口入场研究，**把「空边趋势出场」列为挑战者**，等 owner 点头再谈影子

输出：`analysis/output/trend_exit_base_rate.json` / `.csv`；
标签扩展：`src/judgment/labeling.py`（short trail/MA、structure、time_stop、sl_only）。

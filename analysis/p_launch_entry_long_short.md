# 启动入场：强制多空分边 base rate — 2026-07-23

**纪律**：纯离线，`<2026-05-04`（**未碰 holdout**）；入场=下一根开盘；障碍=**TP5/SL2/72bar**；
成本 maker 0.06% + legacy 0.20%。不 promote、不下单。单变量：**只改测量呈现**（强制
long / short / both），不改 TP/SL/密集阈值/启动窗。

回答 owner：「多空没区分好」——上一轮把跟向多空合成一行 PF；本轮强制分边后，启动
有没有一边过 1.3？

> 上一轮混池报告见 [`p_launch_entry_base_rate.md`](./p_launch_entry_base_rate.md)
> （顶部已链回本文件）。**上一轮 both-side PF 作废作主裁决，仅作历史对照。**

## 审计结论（先于新表）

| 问题 | 结论 |
|---|---|
| 方向如何判定？ | 见下「方向规则」；突破/交叉跟向逻辑**未写反** |
| 是否把 long/short 混成一行 PF？ | **是**——这是测量呈现 bug |
| 入场方向是否用错（上破却开空）？ | **否**。本轮 `direction_audit`：`range_mismatch=0`，`vol_mismatch=0` |
| 多空样本比（跟向变体） | 约 **48% 多 / 52% 空**（range/vol/spread）；MA 交叉略偏多（51.6%） |
| 上一轮 PF 如何处理？ | **测量 bug（混池）→ 上一轮裁决句降权/作废**；方向规则本身不废 |

盘整中 emergence 原只有「固定多」；方向含糊处本轮补：**固定多 | 固定空 | mom24**。
「突破定向」由独立变体 **range / vol 突破跟向**回答（入场移到突破 bar，不是 tip 染色）。

## 复现命令

```bash
PYTHONPATH=. .venv/bin/python scripts/launch_entry_base_rate.py \
  --n-symbols 0 --tag launch_entry_long_short
# 输出: analysis/output/launch_entry_long_short.json
```

## 约定（与上一轮同值）

| 项 | 值 |
|---|---|
| 入场价 | 信号 bar **下一根开盘** |
| 障碍 | TP5 / SL2 / 72；同 bar 双触→SL；`atr_pct≥0.0015` |
| 成本 | maker 0.06% + legacy 0.20% |
| 密集门 | `fast≤0.0028` & `full≤0.0055`，run 刚到 5 |
| 启动窗 | 密集合格后最多 48 根；MIN_GAP=18 |
| 宇宙 | OKX SWAP，剔 stockish / frozen-eval；train only |

## 方向规则（因果、可解释）

| 变体 | 规则 |
|---|---|
| emergence_always_long | tip 固定 +1 |
| emergence_always_short | tip 固定 −1 |
| emergence_mom24 | `sign(close[i]/close[i-24]−1)`；mom=0 跳过 |
| range_break_n20 | 突破后：`close > max(high[i−N:i]) → +1`；`< min(low…) → −1`；N=20 |
| vol_break_n20_k1.5 | 同上 + `volume[i] > mean(vol[i−M:i])×1.5` |
| spread_expand_chg8 | `Δfast_spread(8) ≥ 0.00383`；`close ≥ cluster_mid → +1` 否则 −1 |
| ma_arrange_cross | 优先 ema8×ema21 金/死叉，否则 close×sma20；跟交叉方向 |

标签：`direction>0 → label_candidate`，`<0 → label_short_candidate`（项目标准几何空头收益）。

## 数据统计

| 项 | 值 |
|---|---|
| 币数 | 236 |
| 时间 | 2025-06-05 → 2026-05-03（train） |
| 方向审计 | range/vol mismatch = **0** |

## 对照表（主裁决 = long-only / short-only）

| 变体 | 边 | n | 胜率 | 净@maker | PF@maker | PF@0.2% |
|---|---|---:|---:|---:|---:|---:|
| emergence_always_long | **long** | 16145 | 29.6% | −0.00084 | **0.876** | 0.711 |
| emergence_always_short | **short** | 16145 | 32.7% | +0.00044 | **1.068** | 0.868 |
| emergence_mom24 | long | 7693 | 29.0% | −0.00097 | 0.858 | 0.698 |
| emergence_mom24 | short | 8198 | 33.0% | +0.00051 | 1.080 | 0.877 |
| emergence_mom24 | both† | 15891 | 31.1% | −0.00021 | 0.968 | 0.787 |
| range_break_n20 | long | 7286 | 30.8% | −0.00038 | 0.943 | 0.767 |
| range_break_n20 | short | 7829 | 32.5% | +0.00038 | 1.058 | 0.863 |
| range_break_n20 | both† | 15115 | 31.7% | +0.00001 | 1.002 | 0.816 |
| vol_break_n20_k1.5 | long | 6487 | 30.5% | −0.00040 | 0.941 | 0.769 |
| vol_break_n20_k1.5 | short | 7285 | 32.7% | +0.00060 | 1.090 | 0.893 |
| vol_break_n20_k1.5 | both† | 13772 | 31.7% | +0.00013 | 1.019 | 0.834 |
| spread_expand_chg8 | long | 5800 | 29.9% | −0.00083 | 0.892 | 0.743 |
| spread_expand_chg8 | **short** | 6202 | 35.4% | +0.00169 | **1.245** | **1.037** |
| spread_expand_chg8 | both† | 12002 | 32.8% | +0.00047 | 1.065 | 0.887 |
| ma_arrange_cross | long | 8227 | 28.6% | −0.00138 | 0.805 | 0.656 |
| ma_arrange_cross | short | 7714 | 32.9% | +0.00049 | 1.076 | 0.878 |
| ma_arrange_cross | both† | 15941 | 30.7% | −0.00047 | 0.930 | 0.759 |

† both 仅对照，**不作主裁决**（即上一轮误当主表的那一类数）。

无 long/short 单边 PF@maker > 1.3 → 无泄漏旗。

## 裁决句

**区分多空之后：「启动」两边都未过 1.3；多边全面偏薄，空边相对好但仍薄。**

- **做多**：所有因果变体 PF@maker ∈ **0.81–0.94**，全部 <1.0
- **做空**：最好 = spread 散开跟向 **1.245**；其次 vol 突破空 **1.090**、emergence 固定空 **1.068**
- **没有任何一边 ≥ 1.3**；扣 0.2% 后仅 spread-short 擦过 1.037，其余空边 <1.0，多边更差
- 上一轮「spread both 1.065 最好」是 **多 0.892 + 空 1.245 的糊墙平均**——owner 一眼看出的问题成立

解读：盘整 tip 本身带空头偏置（固定空 1.07 > 固定多 0.88），跟向启动把空边再抠高一点，但粗糙规则仍付不起可交易线。

## 风险与诚实声明

1. **上一轮是测量呈现 bug，不是方向写反**：突破上→多、下→空审计通过；废的是「混池 PF 主裁决」。
2. **未碰 holdout**；本表 train 发现级。
3. **空头几何收益**（`entry/exit−1`）与多头 `exit/entry−1` 在 TP 命中时略不对称——沿用 `labeling.py` 项目标准，本轮不改标签公式。
4. **单阈值未扫参**；不改 thr/k/N/障碍。
5. **spread-short 1.245 仍 <1.3**，且是单边选样后的最好格；不作「空头可交易」叙事。
6. 资金费/借币未建模。

## 下一步选项（需 Owner 决策）

- **A. 停**：分边后仍无边过 1.3；接受「机械启动不够」。
- **B. 深挖 spread-short 单边**（阈值网格）——预期边际，需批准范围；**不承诺过 1.3**。
- **C. 对照 owner 框确认态**（已知另一条线）。
- **D. holdout**：不建议；需单独批准。

测量机建议：**A 为默认**。

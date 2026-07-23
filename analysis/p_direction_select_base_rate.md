# 因果择向 base rate — 2026-07-23

**纪律**：纯离线，`<2026-05-04`（**未碰 holdout**）；入场=下一根开盘；障碍=**TP5/SL2/72bar**；
成本 maker 0.06% + legacy 0.20%。不 promote、不下单、不改 ACTIVE/执行器/三门。
单变量：只改「择向规则」；密集门 / 启动窗 / 障碍 / 成本与
[`p_launch_entry_long_short.md`](./p_launch_entry_long_short.md) 同值，可对照。

**问题**：有密集 tip 后，用因果规则选多或空（不够则跳过），能否让某一边
train PF@maker ≥ 1.3？

**成功标准**（预声明）

| 结果 | 标签 |
|---|---|
| 某一边 train PF@maker ≥ 1.3 | **值得谈影子/继续** |
| 两边都 < 1.3 | **择向未救出可交易边** |

## 复现命令

```bash
PYTHONPATH=. .venv/bin/python scripts/direction_select_base_rate.py \
  --n-symbols 0 --tag direction_select_base_rate
# 输出: analysis/output/direction_select_base_rate.json
#       analysis/output/direction_select_base_rate.csv
```

## 底座与约定

| 项 | 值 |
|---|---|
| 候选底座 | **emergence tip**：`fast≤0.0028` & `full≤0.0055`，run 刚到 5（与 launch 分边同） |
| MA bundle | judgment `add_indicators`（EMA8–55 + 144/200） |
| 入场价 | 信号 bar **下一根开盘** |
| 障碍 | TP5 / SL2 / 72；同 bar 双触→SL；`atr_pct≥0.0015` |
| 成本 | maker 0.06% + legacy 0.20% |
| 启动确认窗 | tip 后最多 48 根（突破 / spread）；MIN_GAP=18 |
| 宇宙 | OKX SWAP，剔 stockish / frozen-eval；train only |
| 主表 | **long \| short 分行**；both 仅对照 |

## 择向规则（因果、预声明默认，未扫参）

| 变体 | 规则 |
|---|---|
| ctrl_fixed_long | tip 固定 +1（无择向） |
| ctrl_fixed_short | tip 固定 −1（无择向） |
| arrange_order_score | tip：`order_score≥3` 且 `>down` → 多；`down_order_score≥3` 且 `>order` → 空；否则跳过（阈值 = `STRICT_THRESHOLDS.order_score_min`） |
| range_break_n20 | tip 后首次突破：`close > max(high[i−N:i]) → 多`；`< min(low…) → 空`；N=20 |
| spread_expand_chg8 | tip 后首次 `Δfast_spread(8) ≥ 0.00383`；`close ≥ cluster_mid → 多` 否则空 |

标签：`direction>0 → label_candidate`，`<0 → label_short_candidate`。

## 数据统计

| 项 | 值 |
|---|---|
| 币数 | 236 |
| 时间 | 2025-06-05 → 2026-05-03（train） |
| tip 原始数 | 19250 |
| 排列择向跳过率 | **43.2%**（8306/19250） |
| 排列择向成交通 | long 4807 / short 4627（两侧 n 充足） |

## 对照表（主裁决 = long-only / short-only）

| 变体 | 边 | n | 胜率 | 净@maker | PF@maker | PF@0.2% |
|---|---|---:|---:|---:|---:|---:|
| ctrl_fixed_long | **long** | 16145 | 29.6% | −0.00084 | **0.876** | 0.711 |
| ctrl_fixed_short | **short** | 16145 | 32.7% | +0.00044 | **1.068** | 0.868 |
| arrange_order_score | long | 4807 | 28.7% | −0.00099 | 0.851 | 0.688 |
| arrange_order_score | short | 4627 | 32.9% | +0.00073 | 1.116 | 0.908 |
| arrange_order_score | both† | 9434 | 30.8% | −0.00015 | 0.978 | 0.793 |
| range_break_n20 | long | 7286 | 30.8% | −0.00038 | 0.943 | 0.767 |
| range_break_n20 | short | 7829 | 32.5% | +0.00038 | 1.058 | 0.863 |
| range_break_n20 | both† | 15115 | 31.7% | +0.00001 | 1.002 | 0.816 |
| spread_expand_chg8 | long | 5800 | 29.9% | −0.00083 | 0.892 | 0.743 |
| spread_expand_chg8 | **short** | 6202 | 35.4% | +0.00169 | **1.245** | **1.037** |
| spread_expand_chg8 | both† | 12002 | 32.8% | +0.00047 | 1.065 | 0.887 |

† both 仅对照，**不作主裁决**。

与 [`p_launch_entry_long_short.md`](./p_launch_entry_long_short.md) 同规则行数字一致
（fixed / range / spread）——底座对齐已核实。

无 long/short 单边 PF@maker ≥ 1.3。

## 裁决句

**择向未救出可交易边。**

- **最好边**：spread 散开跟向空 **PF@maker = 1.245**（仍 <1.3）；扣 0.2% 后 1.037
- **排列择向**：跳过 43% tip 后，空边 1.116、多边 0.851——相对固定空(1.068)仅 +0.05，
  相对固定多更差；**跳过≠选出可交易边**
- **突破择向**：多 0.943 / 空 1.058，贴近固定对照的短偏置，无跃迁
- **多边**：所有规则 PF@maker ∈ **0.85–0.94**，全部 <1.0
- 结论与 launch 分边报告同向：盘整 tip 自带空头偏置；因果择向不能把任一边抬过 1.3

## 解读

1. tip 时刻的 `order_score` 排列是「形态朝向」代理，不是障碍收益预测器——滤掉近半 tip
   后 PF 几乎不动，说明被跳过的多数是噪声而非系统错边。
2. 突破 / spread 把入场挪到确认 bar，空边略好于 tip 固定空，但仍是同一数量级薄边。
3. 小样冒烟（20 币）曾见 spread-short PF@m=1.38——**全量回落到 1.245**；勿用小样过线叙事。

## 风险与诚实声明

1. **未碰 holdout**；本表 train 发现级。
2. **未扫阈值**（order_score_min / N / thr / wait 均预声明默认）；扫参需 owner 另批范围。
3. 空头几何收益沿用 `label_short_candidate`（`entry/exit−1`），与多头略不对称——项目标准，本轮不改。
4. 资金费/借币未建模；实盘空单未开。
5. 与 launch 分边报告共享 spread-short=1.245——**不是新发现的第二根救命稻草**，是同一格的复述。
6. 冒烟 20 币过线是抽样噪声，全量否决。

## 下一步选项（需 Owner 决策）

- **A. 收口（默认）**：择向未过 1.3；与 launch 分边一并接受「机械 tip/启动择向不够」。
- **B. 仅对 spread-short 做阈值网格**——预期边际，**不承诺过 1.3**；需批准扫参范围。
- **C. 换命题**（真实 tip 分布 / owner 框确认态旁路等）——见 HANDOFF 出路。
- **D. holdout**：不建议；需单独批准。

测量机建议：**A**。不值得为这套因果择向开影子仓。

# 入场时机：signal_close vs next_open — 2026-07-23

**纪律**：纯离线，`<2026-05-04`（**未碰 holdout**）；同一套分边因果规则；
障碍固定 **TP5/SL2/72bar**（**未扫 TP/SL**）；成本 maker 0.06% + legacy 0.20%。
单变量：**只改入场价约定**。不 promote、不改 ACTIVE/执行器/三门。

**问题**：项目默认「信号下一根开盘入场」是否必要？若改成「信号 bar 收盘价入场」，
同一规则下 PF 是否明显更好？

**成功标准**（预声明）：某一边某一档 entry 的 train PF@maker ≥ 1.3 → 值得谈影子；
两档皆 <1.3 → 入场约定未救出可交易边。

## 复现命令

```bash
# 对照脚本：一次扫描同时结算两档 entry
PYTHONPATH=. .venv/bin/python scripts/entry_timing_close_vs_next.py \
  --n-symbols 0 --tag entry_timing_close_vs_next

# 亦可单档复用 direction_select：
PYTHONPATH=. .venv/bin/python scripts/direction_select_base_rate.py \
  --n-symbols 0 --entry next_open --tag entry_timing_next_open
PYTHONPATH=. .venv/bin/python scripts/direction_select_base_rate.py \
  --n-symbols 0 --entry signal_close --tag entry_timing_signal_close
```

输出：`analysis/output/entry_timing_close_vs_next.json`、
`…_compare.csv`、`…_next_open.csv`、`…_signal_close.csv`。

## 规则底座（写明选用哪套）

选用 **`direction_select` / `launch` 分边因果规则**（非 owner_side 扩特征 AND），
原因：

1. 已发表 next_open 基线可直接对表（[`p_direction_select_base_rate.md`](./p_direction_select_base_rate.md)）
2. 最好边已知为 spread-short **1.245**——本轮测的是「改入场能否抬过 1.3」
3. owner_side 扩特征规则依赖 LGBM 衍生阈值 + 116 列，混入第二变量

| 项 | 值 |
|---|---|
| 候选底座 | emergence tip：`fast≤0.0028` & `full≤0.0055`，run 刚到 5 |
| 择向规则 | ctrl_fixed / arrange_order_score / range_break_n20 / **spread_expand_chg8** |
| 入场 A | `next_open` = 信号 bar **下一根开盘**（现状） |
| 入场 B | `signal_close` = 信号 bar **收盘价**；障碍路径仍从下一根起算 |
| 障碍 | TP5 / SL2 / 72；同 bar 双触→SL；`atr_pct≥0.0015` |
| 成本 | maker 0.06% + legacy 0.20% |
| 宇宙 | OKX SWAP，剔 stockish / frozen-eval；train only |
| 主表 | **long \| short 分行** |

## 数据统计

| 项 | 值 |
|---|---|
| 币数 | 236 |
| 时间 | 2025-06-05 → 2026-05-03（train） |
| tip 原始数 | 与 direction_select 同底座 |
| next_open 最佳边 n | spread-short **6202** |
| signal_close 同规则 n | **6202**（同信号集；仅成交价不同） |

## 两档 PF 对照表（主裁决 = long / short）

| 变体 | 边 | n | 胜率@n | PF@maker next | PF@maker close | Δ(close−next) | PF@0.2% next | PF@0.2% close |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| ctrl_fixed_long | **long** | 16145 | 29.6% | **0.876** | **0.877** | +0.001 | 0.711 | 0.712 |
| ctrl_fixed_short | **short** | 16145 | 32.7% | **1.068** | **1.064** | −0.004 | 0.868 | 0.865 |
| arrange_order_score | long | 4807 | 28.8% | 0.851 | 0.860 | +0.009 | 0.688 | 0.695 |
| arrange_order_score | short | 4627 | 32.9% | 1.116 | 1.112 | −0.004 | 0.908 | 0.905 |
| range_break_n20 | long | 7286 | 30.8% | 0.943 | 0.953 | +0.010 | 0.767 | 0.776 |
| range_break_n20 | short | 7829 | 32.5% | 1.058 | 1.062 | +0.004 | 0.863 | 0.866 |
| spread_expand_chg8 | long | 5800 | 30.0% | 0.892 | 0.896 | +0.004 | 0.743 | 0.746 |
| spread_expand_chg8 | **short** | 6202 | 35.4% | **1.245** | **1.244** | **−0.001** | 1.037 | 1.037 |

next_open 列与 [`p_direction_select_base_rate.md`](./p_direction_select_base_rate.md) 数字一致（底座对齐核实）。

无 long/short × entry 组合 PF@maker ≥ 1.3。

## 裁决句

**入场约定未救出可交易边。**

- 最好边仍是 spread-short：next_open **1.245** ≈ signal_close **1.244**（Δ≈0）
- 全表 Δ(close−next) ∈ **[−0.004, +0.010]**——噪声级，无系统性抬升
- 多边两档皆 ∈ 0.85–0.95；空边最好仍 <1.3
- **去掉「下一根开盘」限制，在本底座上不改变结论**

## 解读

1. **同一路径、不同成交价**：两档障碍路径都从 `signal_i+1` 起算；差别只在
   成交价（下一开 vs 当根收）→ ATR 倍数障碍绝对价位平移。全市场平均下，
   开-收跳空相对 ATR 的尺度不足以改写 PF 数量级。
2. **小样陷阱复现**：20 币冒烟 spread-short next=1.382 / close=1.359（看似过线）；
   全量回落到 ~1.24——与 direction_select 报告同一教训。
3. **与「为何曾用 next_open」一致**：默认下一开盘不是因为收盘入场会亏很多，而是
   **成交可行性与无同价成交假设**（见下节）；本轮实证也不支持「改成收盘就能过 1.3」。

## 为何曾用 next_open

1. **防同价成交幻觉**：规则在 bar i 收盘才完备（至少 close / 当根指标就绪）；
   若回测用 `close[i]` 成交，等于假设「判定瞬间能按收盘价挂到」——实盘需
   收盘竞价/收盘单，或近似为下一跳市价（又滑向 next_open）。
2. **与文献/项目惯例对齐**：离线 labeling、YOLO 候选、forward 账本主路径长期统一为
   「信号后下一根开盘」；改约定要三处同改，否则回测≠实盘。
3. **tip 实时路径的例外**：盘口 tip 尚未走出下一根时，账本曾用信号收盘作
   **代理价**再回填——那是记账权宜，不是研究层改成 close 入场的理由
  （见 `docs/learnings/tip-rows-record-first-backfill-entry-later.md`）。

本轮结论：**不是「一定必须下一开」才有边，而是换 close 也救不出边**——限制可讨论拿掉，
但拿掉后数字几乎不动。

## 三重障碍优化（另一变量；本轮未跑）

**可以优化，但是另一变量。**

- 改 TP/SL 倍数属铁律（障碍参数），需 owner **另批**；本轮单变量只动 entry。
- 在同 train 数据上扫 TP×SL 网格极易过拟合：会挑到样本内好看的倍数，
  与「入场约定」纠缠后无法归因。
- **本轮未跑任何 TP/SL 网格**——等批准后再单变量扫，并预声明网格范围与成功线。

## 风险与诚实声明

1. **未碰 holdout**；发现级 train 表。
2. **泄漏检查（signal_close）**：range_break / spread_expand 用 **当根 close** 判定方向；
   close 入场 =「收盘判定 + 收盘成交」。这不是偷看未来 bar，但是 **同打印成交假设**。
   实盘要能挂到收盘，或用下一跳近似（近似后又接近 next_open）。本轮 close
   **并未明显更好**，故不存在「靠同价成交假设刷出过线 PF」的问题；若未来某规则
   close 档突然远好于 next，应首先怀疑该假设不可实盘兑现。
3. arrange / fixed 不直接用 close 定方向；spread/range 用 close——诚实写明。
4. 障碍路径两档相同（均从 i+1）；未把信号 bar 盘中 H/L 算进持仓——收盘后已无剩余路径。
5. **未扫 TP/SL**；未改成本档。
6. 未改 ACTIVE / 执行器 / 新鲜度门。

## 下一步选项（需 Owner 决策）

- **A. 收口（默认）**：入场约定不是瓶颈；维持 next_open 作回测↔实盘统一约定即可。
- **B. 批准另开单变量「障碍网格」**——需写清倍数范围与成功线；**不与 entry 混改**。
- **C. 换命题**（真实 tip 分布等）——见 HANDOFF。
- **D. holdout**：不建议；需单独批准。

测量机建议：**A**。

## 产物

- 脚本：`scripts/entry_timing_close_vs_next.py`；
  `scripts/direction_select_base_rate.py --entry {next_open,signal_close}`；
  `src/judgment/labeling.py`（`entry=` 可选参数）
- 数值：`analysis/output/entry_timing_close_vs_next.json`
- 对照 CSV：`analysis/output/entry_timing_close_vs_next_compare.csv`

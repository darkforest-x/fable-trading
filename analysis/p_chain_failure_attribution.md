# 密集链路失败归因 — 哪一层是主因 — 2026-07-23

**纪律**：综合已发表报告 + 本轮 **train-only** 诊断；**未**消耗 holdout#8；
**未** promote / 改 ACTIVE / 真下单。Holdout#7 记账不变。

**诚实边界（写在最上）**

> Holdout#7 已否的是：**当前预注册 A**（`spread_expand_chg8` short + `no_tp`/`trail4`）
> **在 ≥2026-05-04 窗可交易**。  
> **未**否：「整条双均线密集链路永远无边」。信号在 train 上稳定薄正、胜过随机；
> 塌的是**迁移**，不是测量 bug（见 holdout bug audit）。

## 裁决句（醒目）

| 层 | 支持度 | 是否已排除 | 一句话 |
|---|---|---|---|
| **Regime / 边不迁移** | **强** | **否（主因）** | train 利润堆在 2025H2–2026Q1；2026-04 翻车预警；holdout 三月全 ~1.0 |
| **入场规则 ≠ 眼睛** | **强** | **否（主因并列）** | 手标 short 与规则 Jaccard≈0.04–0.05；owner 召回仅 ~25%；oracle ΔPF 5–15 |
| **出场（TP/SL/趋势出）** | 中（train 放大器） | **作为 holdout 塌因：已排除** | train 抬 +0.17 PF；holdout 两档同步塌 → 不是出场参数运气 |
| **特征 / 多因子打分** | 弱（救不了） | **作为「能救出当前入场」：基本排除** | rich AND 几乎不抬 PF；WF 分数门仍躲不过 2026-04 |

**主因排序**：① **regime 换挡导致 train 边不迁移** ② **因果入场与 owner 手感严重不对齐**
→ ③ 出场只是 train 内放大器（holdout 同塌已排除主责）→ ④ 打分/堆因子不能救当前触发器。

---

## 复现（本轮诊断）

```bash
PYTHONPATH=. .venv/bin/python scripts/chain_failure_attribution.py \
  --n-symbols 0 --tag chain_failure_attribution
# 输出:
#   analysis/output/chain_failure_attribution.json
#   …_A.csv / _B.csv / _C_wf_months.csv / _D_overlap.csv
```

Train only：`open_time < 2026-05-04`；币 233；与 `p_short_trend_ab` 同宇宙。

---

## 1. 出场层 — 支持度中；**holdout 主因已排除**

### 已有证据

| 源 | 结论 |
|---|---|
| `p_trend_exit_base_rate` | 固定 spread 入场，空边 `no_tp`/`trail3`/`ema55` train 过 1.3；**多边换出场仍全负** |
| `p_short_trend_ab` | 四套趋势出月度过线；季度集中 + 2026-04 翻车 |
| `p_short_trend_holdout7` | **no_tp 与 trail4 同步塌到 ~1.0**（0.997 / 0.969） |
| holdout bug audit | 同机随机入场 holdout PF≈1.15 > 规则 0.997 → **非结算 bug** |

### 本轮 A：入场 vs 出场抬 PF（train）

固定 `spread_expand` short：

| 出场 | n | PF@maker | Δ vs baseline |
|---|---:|---:|---:|
| baseline TP5/SL2 | 6202 | 1.245 | — |
| **no_tp_sl2** | 6166 | **1.415** | **+0.170** |
| trail4 | 6166 | 1.359 | +0.114 |
| ema55 | 6166 | 1.316 | +0.071 |

固定最好出场 `no_tp`，换入场：

| 入场 | n | PF@maker | Δ vs fixed_short |
|---|---:|---:|---:|
| ctrl_fixed_short | 16059 | 1.285 | — |
| arrange short | 4609 | 1.275 | −0.010 |
| range_break short | 7788 | 1.252 | −0.033 |
| **spread_expand** | 6166 | **1.415** | **+0.130** |

**读数**：train 上出场抬幅（+0.17）略大于入场抬幅（+0.13）——两者都是**同数量级放大器**，
都不足以单独解释「从 1.4 → holdout 1.0」。Holdout 两档出场同塌 → **主因不在出场旋钮**。

| 判定 | |
|---|---|
| 支持度 | 中（train 有真实抬升；`fixed-tp-cuts-short-trend-edge` 仍成立） |
| 已排除？ | **作为 holdout#7 失败主因：是** |
| 仍可能？ | 将来若有**可迁移入场底座**，趋势出仍可能是正确放大器 |

---

## 2. 入场层 — 支持度强；**未排除，主因并列**

### 已有证据

| 源 | 结论 |
|---|---|
| `p_base_rate_dense` | 密集几何真实薄信号（PF 0.874 > 随机 0.864），毛已近零 |
| `p_launch_entry_long_short` / `p_direction_select` | 最好因果边 = spread-short **1.245**，仍 <1.3；多边全 <1 |
| `p_entry_timing` | close vs next_open Δ≈0 — **入场价约定不是问题** |
| `p_short_trend_ab` B | oracle short PF 6.6–16.8 vs 规则 1.25–1.42（ΔPF 5–15） |

### 本轮 D：手标 short vs `spread_expand` 触发重叠

| 窗（±bars） | n_owner | n_rule | owner 召回 | rule 精度 | Jaccard≈ | 命中中位\|Δ\| |
|---|---:|---:|---:|---:|---:|---:|
| 0（同 bar） | 1284 | 6213 | **5.9%** | 1.2% | **0.010** | 0 |
| 8 | 1284 | 6213 | 24.1% | 5.0% | 0.043 | 1 |
| **18**（=MIN_GAP） | 1284 | 6213 | **25.2%** | **5.2%** | **0.045** | 1 |
| 48 | 1284 | 6213 | 27.3% | 6.0% | 0.049 | 2 |

**读数**：

1. 规则火点 ≈ owner 框的 **4.8×**——大量「眼睛不会开」的触发仍进池。
2. 即便放宽到 ±18 bar，owner short 仍有 **~75% 未被规则覆盖**。
3. Jaccard <0.05 → **入场规则标的不是你眼睛标的那类空**；oracle 优势主要来自
   选点集合差异（+ 确认态 hindsight），不是同一触发器换出场能抹平的。

| 判定 | |
|---|---|
| 支持度 | **强** |
| 已排除？ | **否** — 这是「规则边薄 + 与手感脱节」的结构原因 |
| 仍可能？ | 是：需要**新的因果入场**（更贴近可部署手感，或显式接受「确认散开」叙事并重测） |

---

## 3. 特征 / 多因子打分 — 支持度弱；**「能救当前触发」基本排除**

### 已有证据

| 源 | 结论 |
|---|---|
| `p_owner_side_feature_verdict` | 分边因果 short 1.127 <1.3 |
| `p_owner_side_rich_features` | 116 因子 short 1.227 仍 <1.3；top 仍是 order+spread |
| `p_samesource_judgment` | 同源判断 walk-forward 不稳；edge 随 regime 摆 |
| `p_v16_holdout` | 判断层反预测（事后分布训 → tip 分布亏） |

### 本轮 C：在 **同一** spread-short 触发上过滤

底座：spread short + `no_tp`（train PF 1.415）。

| 过滤 | n | PF@maker | 对 2026-04 |
|---|---:|---:|---|
| 无过滤 | 6166 | 1.415 | 月 PF **0.678** |
| rich short AND（简化：`close_vs_sma20≤-1.02%` ∧ `order≤0` ∧ `spread_chg8≥0.004`） | 3052（过半） | **1.417**（+0.002） | — |
| WF 分数门（先验月 score 中位，当月 ≥thr） | 2955 | 1.481 | 4 月 0.678→**0.899** 仍亏 |

**读数**：打分能在顺风月锦上添花，**抬不动翻车月过 1.0**；rich AND 对全样本几乎零贡献。
与「堆 OHLCV 救不出分边规则」同构——**瓶颈不在本地因子空间**。

| 判定 | |
|---|---|
| 支持度 | 弱（作救援手段） |
| 已排除？ | **作为「把当前 spread 触发打出可迁移边」：基本是** |
| 仍可能？ | 极弱：除非换数据源（资金费/order flow）或换标签定义；继续堆 OHLCV **不值得** |

---

## 4. Regime — 支持度强；**主因，未排除**

### 已有证据

| 源 | 结论 |
|---|---|
| `p_short_trend_ab` | 2026Q1 独占 no_tp 净利 **64%**；2026Q2 train PF **0.659** |
| holdout#7 | 5/6/7 三月 PF 皆 ~1.0（trail 仅 5 月擦 1.16） |
| `p_base_rate_dense` / samesource | 月度 PF 随行情剧烈摆动 |

### 本轮 B：波动 / BTC 切片（spread short + no_tp，train）

**ATR 分位（信号 bar）**

| 片 | n | PF@maker | 净合计@m |
|---|---:|---:|---:|
| atr_q1 | 1542 | 1.170 | +1.62 |
| atr_q2 | 1541 | 1.113 | +1.36 |
| **atr_q3** | 1541 | **1.675** | +9.07 |
| **atr_q4** | 1542 | **1.555** | +9.76 |

高波动两档吃掉绝大部分净利；低波两档擦线。

**BTC 趋势（ret96）**

| 片 | n | PF@maker |
|---|---:|---:|
| btc_down（≤−3%） | 263 | **1.546** |
| btc_flat | 5768 | 1.431 |
| **btc_up（≥+3%）** | 135 | **0.580** |

空边在 BTC 急涨窗系统性翻车——与「盘整 tip 空偏」叙事一致，也解释持有期撞上
风险-on 时的尾损。

| 判定 | |
|---|---|
| 支持度 | **强** |
| 已排除？ | **否** — 最干净解释 holdout 均值回归到 ~1.0 |
| 仍可能？ | 是：regime 门（atr / BTC）可作**单变量发现实验**（仍 train-only），过线后再谈预注册 |

---

## 主因排序（综合）

```
holdout#7 塌因
├─ ① Regime：train 边制度相关，不迁移（强）
├─ ② 入场：规则集合 ≠ 眼睛集合，因果触发过宽/错位（强）
├─ ③ 出场：train 放大器；holdout 同塌 → 排除主责
└─ ④ 打分：救不了当前触发与翻车月 → 基本排除救援路径
```

**不是**「测量算错了」；**不是**「再调 trail 倍数就能过」；**不是**「再加 50 个因子」。

---

## 对照：整条链路各阶段诚实位置

| 阶段 | 结论级 | PF 量级 | 含义 |
|---|---|---|---|
| 密集几何 base | 真信号、太薄 | ~0.87 | 命题非零 alpha |
| 启动/择向最好边 | 发现级 | spread-short 1.25 | 未过 1.3 |
| +趋势出 | train 过线 | 1.32–1.42 | **未迁移**（holdout#7） |
| owner 分边/富特征因果 | 未过线 | ≤1.23 | 打分救不出 |
| owner oracle | 事后 | 5–17 | 禁止当部署证据 |
| v16 tip-replay | holdout#6 | 0.78 | 检测≠可交易启动 |

---

## 不放弃前提下：下一批诊断（单变量 · 仍不碰 holdout）

每条只改一个变量；过发现线后再谈**新预注册**（不是同一 A 再烧）。

| # | 实验 | 单变量 | 成功线（预声明） | 否决信号 |
|---|---|---|---|---|
| **E1** | **入场对齐**：在 tip 后窗内找更接近 owner short 时点的因果规则（例如确认态：spread+order+ret 显式「已在跌」），禁止用框右缘信息 | 只改入场定义 | train PF@maker≥1.3 且与 owner ±18 召回 ≥0.45 或 Jaccard≥0.12 | 召回/Jaccard 仍低且 PF 不升 |
| **E2** | **Regime 门**：固定现有 spread-short+no_tp，只加 `atr∈q3∪q4` 或 `not btc_up` | 只加一门 | 过线且 ≥2 个差月（含 2026-04 类）PF≥1.0 | 只删掉坏月样本、全样本不升 |
| **E3** | **触发稀疏化**：提高 spread 阈值 / 加 MIN_GAP，使 n 降到接近 owner 量级再结算 | 只改触发密度 | PF≥1.3 且月度不单季独撑 | PF 不升或更脆 |
| **E4** | **出场对照冻结**：新入场一律先用 baseline TP5/SL2 **和** no_tp 双报，禁止一上来只报趋势出 | 呈现纪律 | — | 避免把出场抬升误当入场胜利 |
| **停** | 继续 116+ OHLCV 堆特征 / 同源判断再训 / 同一 A 申请 holdout#8 | — | — | **不做** |
| **旁路** | tip 金标 + v17（纪律 12） | 独立轨 | tip-smoke 门 | 与本归因并行，不互相续命 |

**申请 holdout 的前置**：新预注册假设必须同时满足 (a) train 过线 (b) 写清与 A 的单变量差异
(c) 有 regime 压力测试（至少含一个 train 内翻车月仍不崩，或显式 regime 门）。
否则继续烧 holdout = 重复证伪。

---

## 风险与诚实声明

1. 本报告 **未** 消耗新 holdout；数值复用 holdout#7 + train 诊断。
2. Jaccard 用「同币种最近邻 ≤窗」近似；非严格集合交——量级结论稳健（≪0.1）。
3. rich AND 本轮用了已发表阈值的**简化三条件**（缺 `fast_spread_chg4`/`gap_sma60_120`）；
   全 AND 只会更严，不太可能 magically 抬 PF（对照富特征全市场扫描已 <1.3）。
4. BTC/atr 切片是**事后观察**，E2 必须当作新发现实验，禁止把本表直接写成可部署门。
5. 「不放弃链路」≠「不放弃当前 A」——A 已 holdout 证伪；放弃的是配置，不是命题。

## 产物

- 报告：`analysis/p_chain_failure_attribution.md`（本文件）
- 脚本：`scripts/chain_failure_attribution.py`
- 数值：`analysis/output/chain_failure_attribution.json` + `_A/_B/_C/_D` CSV
- 上游：`p_short_trend_holdout7` / `p_short_trend_ab` / `p_trend_exit_base_rate` /
  `p_owner_side_*` / `p_base_rate_dense` / `p_direction_select` / `p_launch_entry_long_short` /
  `p_entry_timing` / `p_v16_holdout` / `p_samesource_judgment`
- learning：`docs/learnings/chain-failure-is-regime-plus-entry-mismatch.md`

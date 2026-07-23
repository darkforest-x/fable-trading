# E1 入场对齐 owner short + E2 regime 门 — 2026-07-23

**纪律**：train only `open_time < 2026-05-04`；**未**消耗 holdout#8；**未**
promote / 改 ACTIVE / 三门 / 真下单。E1 与 E2 **分开归因**，禁止打包成一个魔法组合。

**成功线（发现级，预声明）**

| 项 | 线 |
|---|---|
| 重叠 | 相对 `spread_expand` short：owner 召回 ≥0.45 **或** Jaccard≥0.12 |
| 因果边 | 全市场扫描 PF@maker ≥1.3（过线只作发现，**不**自动申请 holdout） |
| 压力 | 是否减轻 2026-04 翻车 |
| E2 否决 | 只删坏月样本、全样本不升；或砍到 n_frac≪0.25 |

---

## 中文裁决（醒目）

| 实验 | 裁决 |
|---|---|
| **E1** | **抬了召回，没抬对齐质量；因果边仍死。** 召回 25%→94%（过召回线），但 Jaccard **更差**（0.045→0.018）——规则海量假阳淹没精度。主口径 `no_tp` PF@maker **~1.14 ≪ spread 的 1.415**，未过 1.3。 |
| **E2** | **不是「只砍坏月却砍光样本」。** `not_btc_up` 几乎空转（+0.02 PF，4 月更差）；`atr_q34` 留 50% 样本、全样本 PF 1.415→**1.607**，但 2026-04 仍 **0.845&lt;1**——抬整体、救不了翻车月。 |

**不要**为 E1 新规则或 E2 atr 门申请 holdout#8。

---

## 复现

```bash
PYTHONPATH=. .venv/bin/python scripts/entry_align_and_regime.py \
  --n-symbols 0 --tag entry_align_and_regime
# 输出:
#   analysis/output/entry_align_and_regime.json
#   …_E1_main.csv / …_E2_main.csv / …_overlap.csv
```

| 项 | 值 |
|---|---|
| 币 | 233 OKX USDT-SWAP（去 stockish / eval） |
| owner short（train） | 1361 框 → 拟合命中 1284 cut |
| 拟合 | pos 1284 / neg 5136；LGBM AUC **0.972（仅披露）** |
| 时间 | 2025-06-05 → 2026-05-03 |
| 成本 | maker 0.06% + legacy 0.20% |
| 出场双报 | baseline TP5/SL2、`no_tp_sl2_h144`、`trail4_atr_h144` |

### 预声明规则（E1，从 owner short vs 随机负样本拟合）

| ID | 规则 | 来源 |
|---|---|---|
| **R1_stump** | `order_score≤0` | Youden 最优单桩 |
| **R2_and3** | `order_score≤0` ∧ `close_vs_sma20≤−1.01%` ∧ `fast_spread_chg4≥0.0030` | top-3 gain AND |
| **R3_confirm** | `close_vs_sma20≤−1.01%` ∧ `order_score≤0` ∧ `spread_chg8≥0.0040` ∧ `ret_8≤−1.04%` | 「已在跌 + 散开」叙事 |
| 对照 | `spread_expand_chg8` short | 旧因果入场 |

诚实：规则在 owner 时刻拟合 → **train 重叠偏乐观**；终审只认全市场因果扫描 PF。

---

## E1 — 重叠 vs 因果边（分开读）

### 重叠 @ ±18 bar（=MIN_GAP）

| 入场 | n_rule | owner 召回 | rule 精度 | Jaccard≈ | vs spread |
|---|---:|---:|---:|---:|---|
| **spread_expand** | 6213 | **25.2%** | **5.2%** | **0.045** | — |
| R1_stump | 156975 | 99.8% | 1.6% | 0.008 | 召回↑ Jaccard↓ |
| R2_and3 | 72793 | 94.9% | 2.1% | 0.017 | 同上 |
| **R3_confirm** | 66865 | **94.4%** | 2.2% | **0.018** | 同上 |

**读数**：召回线（≥0.45）三条新规则都过；**Jaccard 线（≥0.12）全灭**，且全面劣于
spread。机制是「放宽到覆盖眼睛」变成「几乎处处可空」——火点是 owner 的 **50×**，
精度从 5% 掉到 2%。**重叠提升 ≠ 集合对齐。**

### 因果 PF（主表 short；主口径 = no_tp）

| 入场 | 出场 | n | PF@maker | PF@0.2% | 2026-04 PF | ≥1.3? |
|---|---|---:|---:|---:|---:|---|
| spread_expand | baseline | 6202 | 1.245 | 1.037 | 0.899 | 否 |
| **spread_expand** | **no_tp** | **6166** | **1.415** | **1.222** | **0.678** | **是（旧）** |
| spread_expand | trail4 | 6166 | 1.359 | 1.141 | 0.962 | 是（旧） |
| R1_stump | no_tp | 155803 | 1.119 | 1.004 | 0.961 | 否 |
| R2_and3 | baseline | 72704 | 1.130 | 1.010 | 1.229 | 否 |
| R2_and3 | no_tp | 72604 | 1.140 | 1.038 | **1.372** | 否 |
| R2_and3 | trail4 | 72604 | 1.313 | 1.190 | 1.542 | 发现擦线* |
| R3_confirm | baseline | 66793 | 1.142 | 1.022 | 1.293 | 否 |
| **R3_confirm** | **no_tp** | **66697** | **1.141** | **1.040** | **1.377** | **否** |
| R3_confirm | trail4 | 66697 | 1.348 | 1.222 | 1.532 | 发现擦线* |

\*trail4 擦线过 1.3：**不得**写成「入场胜利」——同规则 baseline≈1.14，抬升来自出场；
且 holdout#7 已证明 trail4 在旧入场上不迁移。本轮**不**申请 holdout。

**E1 归因句**

1. **重叠**：召回大幅抬升（过线）；Jaccard/精度恶化 → 「对齐眼睛」失败，只是覆盖膨胀。
2. **因果边**：主口径 `no_tp` 全面 **~1.14**，相对 spread **倒退**；边仍死。
3. **反直觉**：宽确认规则在 **2026-04 反而过线**（~1.37），而 spread 同月 0.68——
   说明 4 月翻车更像「窄 tip-散开触发 × regime」交互，不是「凡是空都亏」。
   但这不能兑换成可部署边（全样本更薄 + 假阳爆炸）。

---

## E2 — regime 门（固定旧 spread_expand + no_tp）

| 门 | n | n 占比 | PF@maker | ΔPF | 2026-04 PF | Δ4月 |
|---|---:|---:|---:|---:|---:|---:|
| 无门 | 6166 | 100% | **1.415** | — | **0.678** | — |
| **not_btc_up** | 6031 | **98%** | 1.438 | +0.023 | 0.652 | −0.026 |
| **atr_q34**（≥信号 atr 中位） | 3083 | **50%** | **1.607** | **+0.192** | 0.845 | +0.167 |

**E2 归因句**

1. **`not_btc_up`**：归因报告里 btc_up 片 PF 0.58 很吓人，但全样本占比极低 →
   去掉后几乎不动；**救不了 4 月**（甚至略差）。单变量门 ≈ 空操作。
2. **`atr_q34`**：真实抬全样本 PF（+0.19），样本减半但非砍光；4 月从 0.68→0.85
   **仍亏**。符合「高波吃利润」切片，**不符合**「加门即可迁移 / 躲过翻车月」。
3. **否决信号对照**：不是「只砍坏月」；也不是 n_frac&lt;0.25。更准确：**抬 train 均值、
   不修 regime 裂缝**——与 holdout#7 塌因同构，**不够资格预注册新 holdout**。

### 对照行：最好 E1（R3）+ regime（禁止当组合成功）

| 门 | n | PF@maker | 2026-04 |
|---|---:|---:|---:|
| R3 无门 | 66697 | 1.141 | 1.377 |
| R3 + not_btc_up | 64995 | 1.138 | 1.393 |
| R3 + atr_q34 | 33349 | 1.150 | 1.998 |

读法：在已死的宽入场上再加 atr 门，全样本仍 ~1.15；4 月更好只是宽规则本就偏空
确认态。**不构成 E1×E2 打包过线。**

---

## 与上游对照

| 源 | 本轮关系 |
|---|---|
| `p_chain_failure_attribution` | E1/E2 即该报告预声明下一批；重叠基线复现一致（召回 25.2% / J 0.045） |
| `p_short_trend_ab` / holdout#7 | spread+no_tp train 1.415 复现；本轮不重测 holdout |
| `p_owner_side_rich_features` | top gain 仍是 order + close_vs_sma + spread_chg；扩扫描仍 &lt;1.3 |

---

## 风险与诚实声明

1. **未**动 holdout；过 1.3 的仅旧 spread 趋势出（已 holdout 证伪）与 E1×trail4 擦线（出场抬升，不申请）。
2. 规则拟合用 owner short 时刻 → 召回乐观；即便如此 Jaccard 仍崩，结论更硬。
3. Jaccard 为「同币最近邻命中」近似（与归因脚本同构）；量级稳健。
4. `atr_q34` 阈值 = **本轮** spread 火点 atr 中位（事后观察门）——若预注册必须先验冻结。
5. long 未重跑；既有分边结论 long 全 &lt;1，本轮不干扰 short 裁决。

---

## 下一步（需 owner 决策）

| # | 选项 | 建议 |
|---|---|---|
| 1 | 为 E1 新规则 / E2 atr 门申请 holdout#8 | **否** — 入场边死或门不修 4 月 |
| 2 | E3 触发稀疏化（抬 spread 阈 / 加 MIN_GAP 使 n≈owner 量级） | 可考虑；单变量 |
| 3 | 接受「确认态空」叙事但强制稀疏（R3 ∩ tip 后窗 ∩ 更高阈） | 可；须预声明稀疏约束防假阳 |
| 4 | 旁路 tip 金标 / v17 | 独立轨，与本结论并行 |
| 停 | 继续堆 OHLCV / 同一 A 再烧 holdout | **不做** |

---

## 产物

- 报告：`analysis/p_entry_align_and_regime.md`（本文件）
- 脚本：`scripts/entry_align_and_regime.py`
- 数值：`analysis/output/entry_align_and_regime.json` + `_E1_main` / `_E2_main` / `_overlap`
- learning：`docs/learnings/owner-align-raises-recall-not-jaccard-edge-stays-dead.md`

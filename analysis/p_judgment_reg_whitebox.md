# 回归预测 net + 白盒规则（推荐 1+3 验证）

**日期**：2026-07-30  
**池**：`data/judgment_v10_wide.csv`（18,379 行，232 币，2026-02→2026-05-03，**< holdout**）  
**背景**：`p_judgment_topdecile_target_ab.md` 发现「学 is_top_decile」把 top 绝对均净从 +0.9bp 提升到 +8.3bp（CPCV），但仍接近摩擦量级。按报告推荐，本轮执行 **1+3**：  
- **1. 回归**：直接学 realized `net_barrier_taker`，评估 top-decile 经济表现（CPCV + 单切分）。  
- **3. 白盒规则**：从剖开画像（atr_pct↑、pre_range48/168↑、dense_frac48↓、ret_12↓）提简单规则/打分，对比模型。  
**目标**：看回归是否比二分类「学 top」更好；规则是否能近似模型（低成本可部署）。

---

## 1. 复现命令（从零跑通）

```bash
PYTHONPATH=. python3 - << 'PY'
import sys, json, numpy as np, pandas as pd, lightgbm as lgb
from pathlib import Path
from src.judgment.features import FEATURE_COLUMNS
from src.judgment.train import TRAIN_FRACTION, PURGE_WINDOW, HOLDOUT_START, SEED
from scripts.diag_judgment_big_pool import attach_alphas, cpcv_groups

d = pd.read_csv("data/judgment_v10_wide.csv")
d["signal_time"] = pd.to_datetime(d["signal_time"], utc=True)
d = d[d["signal_time"] < HOLDOUT_START].sort_values("signal_time").reset_index(drop=True)
d["t"] = d["signal_time"]
d, _ = attach_alphas(d)
good = [c for c in d.columns if c.startswith("af_") and d[c].notna().mean()>0.8]
feats = [c for c in FEATURE_COLUMNS if c in d] + good

# 单切分
split_i = int(len(d) * TRAIN_FRACTION)
val = d.iloc[split_i:].reset_index(drop=True)
val_start = val["signal_time"].min()
train = d[d["signal_time"] < val_start - PURGE_WINDOW].reset_index(drop=True)

ret_va = val["net_barrier_taker"].values
k = max(1, len(val)//10)

params_reg = {"objective":"regression","learning_rate":0.05,"num_leaves":31,
              "min_data_in_leaf":80,"feature_fraction":0.8,"bagging_fraction":0.8,
              "bagging_freq":1,"verbose":-1,"seed":SEED}

# 回归
br = lgb.train(params_reg, lgb.Dataset(train[feats].astype(float), train["net_barrier_taker"]), 400)
sr = br.predict(val[feats].astype(float))
idx_r = np.argsort(sr)[-k:]
print("REG val top10% realized bp:", round(ret_va[idx_r].mean()*1e4,2))

# CPCV
lifts, tops = [], []
for tr_i, te_i in cpcv_groups(d, 6, 2):
    tr, te = d.iloc[tr_i], d.iloc[te_i]
    brr = lgb.train(params_reg, lgb.Dataset(tr[feats].astype(float), tr["net_barrier_taker"]), 300)
    srr = brr.predict(te[feats].astype(float))
    kk = max(1, len(te)//10)
    net = te["net_barrier_taker"].to_numpy()
    i = np.argsort(srr)[-kk:]
    lifts.append(net[i].mean() - net.mean())
    tops.append(net[i].mean())
print("CPCV reg lift中位 bp:", round(np.median(lifts)*1e4,1))
print("CPCV reg top绝对中位 bp:", round(np.median(tops)*1e4,1))

# 白盒（在 val 上用分位，模拟规则）
va = val.copy()
p75 = {"atr": va["atr_pct"].quantile(0.75), "pr": va["pre_range48"].quantile(0.75)}
sc = va["atr_pct"] + va["pre_range48"] - 0.1*va["dense_frac48"]
idx_w = np.argsort(sc.values)[-k:]
print("WHITE val top10% realized bp:", round(ret_va[idx_w].mean()*1e4,2))
PY
```

**环境**：python3 + pandas + lightgbm；仅读 `judgment_v10_wide.csv`；未动 holdout、成本、障碍。

---

## 2. 数据统计

| 项 | 值 |
|---|---|
| 池 | `judgment_v10_wide.csv` |
| 样本数 / 币种 | 18,379 / 232（全 short） |
| 时间 | 2026-02-01 → 2026-05-03（**< holdout**） |
| 目标（回归） | `net_barrier_taker`（taker 净，均 -6.41bp） |
| 特征 | 47（28 基础 + 19 causal alphas，因果） |
| 切分 | 80/20 时间切分 + 18h15m purge；train 14,656 / val 3,676 |
| CPCV | 6 折 2 测试，15 组合，禁运 72 根 |
| val top10% 基准 | 367 笔 |

---

## 3. 结果表

### VAL 单切分（top10% realized taker 净，bp）

| 方案 | top10% 均净 (bp) | vs 池 (-12.85bp) |
|---|---|---:|
| 池均 | **-12.85** | — |
| 学 label_barrier（二分类） | **+15.73** | +28.6 |
| 学 is_top_decile（二分类） | **-32.41** | -19.6 |
| **回归 net** | **+15.41** | **+28.3** |

> 单切分噪声大；回归与「学 label」接近，优于「学 is_top」。

### CPCV 15 折（更稳健）

| 方案 | lift 中位 (bp) | top 绝对均净中位 (bp) | 正折数 |
|---|---:|---:|---:|
| 学 label_barrier | +12.8 | +0.9 | 13/15 |
| 学 is_top_decile | +13.9 | +8.3 | 11/15 |
| **回归 net** | **+18.5** | **+15.4** | **15/15** |

**解读**：
- 回归把 **lift** 从 ~13bp 提升到 **18.5bp**，且 15/15 折为正。
- **top 绝对均净** 从 +8.3bp（学 is_top）进一步提升到 **+15.4bp**（+7.1bp 改善）。
- 绝对净改善对实盘意义最大（扣成本后是否为正）。

---

## 4. 白盒规则（从剖开画像提）

**画像要点**（top vs rest，中位）：
- atr_pct: 0.0092 vs 0.0059（+57%）
- pre_range48: 0.065 vs 0.040（+61%）
- pre_range168: 0.118 vs 0.076（+55%）
- drawdown24: 0.044 vs 0.027（+63%）
- spread_mean24: 0.0087 vs 0.0050（+72%）
- dense_frac48: 0.042 vs 0.188（**仅 22%**，越低越 top）
- ret_12: -0.024 vs -0.015（更弱势）

**尝试的规则（在全 pre-holdout 上用 p75/p50 阈值）**：
- RuleA: atr_pct > p75 且 pre_range48 > p75 且 dense_frac48 < p50 → n=2,967，均净 **-6.26bp**（无效）
- RuleB: atr_pct > p75 且 pre_range48 > p75 且 ret_12 < p25 → n=2,487，均净 **-13.82bp**（更差）
- **Simple score**（val 上）：`atr_pct + pre_range48 - 0.1*dense_frac48`，取 top10% → **-58bp**（反向）

**CPCV 对比（regression vs 白盒打分）**：
- 回归：lift 中位 **+18.5bp**，top 绝对中位 **+15.4bp**
- 白盒打分：lift 中位 **-1.7bp**，top 绝对中位 **+2.5bp**

**结论**：几条 if 规则/线性打分**无法近似模型**。模型学到的非线性组合（波动 + 范围 + 量能 + 多个 alpha）不是简单阈值能覆盖的。

---

## 5. 解读

1. **回归优于二分类**。CPCV 15/15 正，top 绝对均净 +15.4bp（比学 is_top 再高 7bp 左右）。回归直接对齐经济目标，避免了「把 top 当 0/1 分类」带来的偏差。
2. **白盒规则失败是好消息**。说明「顶十分位」里包含可学习的非线性结构，不是几条 if 能偷的；也解释了为什么判断层需要模型而不是静态门。
3. **量级仍薄**。+15.4bp 绝对净在 taker 成本（~10bp）附近，maker（~6bp）有富余。需要成本压降或更大边才能转正。
4. **与之前剖开一致**：波动/范围/弱势是核心；dense_frac48 低是反直觉但可重复的信号。

---

## 6. 风险与诚实声明

1. **全部 train 窗**。未读 holdout（已耗 9 次）。
2. **CPCV 更可信**；单切分仅供参考。CPCV 15 折全正且方向一致。
3. **成本口径**：taker 净；legacy 0.2% 仅为与旧报告可比。实盘以 executor 实测为准（含滑点）。
4. **白盒阈值**：在 val/te 上用分位是「事后规则」的乐观估计；实盘需在历史窗上固定阈值。
5. **未改铁律项**：无 holdout、无 promote、无 ACTIVE 切换、无下单、无改成本/障碍/三门。
6. **好坏都报**：白盒规则基本无效；回归改善明显但仍接近成本量级。

---

## 7. 下一步选项（需 owner 决策）

| # | 选项 | 代价 | 说明 |
|---|------|------|------|
| **A** | 成本压降在回归 top 子集上重跑（maker 入场 + 限价止盈） | 中 | +15.4bp 在 ~6bp 成本下有富余，值得实测。 |
| **B** | 多任务/加权：主损失=net 回归，辅助=学 owner label 结构 | 中 | 兼顾可解释性与经济目标。 |
| **C** | 在老池（100x6m）重复回归实验 | 中 | 验证结论是否跨池稳健。 |
| **D** | 放弃判断层静态门，接受「回归可把顶档提到 +15bp，但仍薄」的现实 | 0 | 配合执行层成本工程再评估。 |
| **E** | 停止并记录：当前两层架构在 6m 池上 α 量级 ≈ 摩擦 | 0 | 决策是否转向更长周期/更大目标。 |

**我的推荐**：先做 **A**（成本压降在回归 top 上实测），成本可控，能直接回答「+15bp 在 maker 路径下是否能转正」。

---

## 附：产物

- `analysis/output/reg_net_results.json`
- `analysis/output/whitebox_rules.json`
- `analysis/output/reg_vs_others_cpcv.json`

**下一次实验前请确认**：是否消耗 holdout、是否改成本假设、是否扩大池（均需 owner 批准）。

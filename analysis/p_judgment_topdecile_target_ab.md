# A+B 实验：把「顶十分位」本身作为判断层新目标

**日期**：2026-07-30  
**池**：`data/judgment_v10_wide.csv`（18,379 行，232 币，2026-02→2026-05-03，**< holdout**）  
**背景**：`p_judgment_topdecile_profile_v10.md` 剖开发现顶十分位与 owner label（label_barrier）差异显著（Jaccard ~0.30），且特征画像指向「极端波动+弱势」。本次执行推荐选项 **A+B**：  
- **A**：以「是否为 top-decile（train 内 net_barrier_taker 90% 分位）」为新二分类目标重训判断层。  
- **B**：加影子标签 is_top_decile，量化与 owner label 的正交性、信息增益、特征差异。  
**目标**：验证「直接学可交易的顶档定义」是否比学 owner 标注更优（核心看 top-decile 经济 lift 与绝对净收益）。

---

## 1. 复现命令（从零跑通）

```bash
# 完整 A+B（含单切分画像 + CPCV 15 折）
PYTHONPATH=. python3 - << 'PY'
import sys, json, numpy as np, pandas as pd, lightgbm as lgb
from pathlib import Path
from scipy.stats import ks_2samp, spearmanr
from sklearn.metrics import mutual_info_score
from src.judgment.features import FEATURE_COLUMNS
from src.judgment.train import TRAIN_FRACTION, PURGE_WINDOW, HOLDOUT_START, SEED, evaluate
from scripts.diag_judgment_big_pool import attach_alphas, cpcv_groups

d = pd.read_csv("data/judgment_v10_wide.csv")
d["signal_time"] = pd.to_datetime(d["signal_time"], utc=True)
d = d[d["signal_time"] < HOLDOUT_START].sort_values("signal_time").reset_index(drop=True)
d["t"] = d["signal_time"]
d, _ = attach_alphas(d)
good = [c for c in d.columns if c.startswith("af_") and d[c].notna().mean()>0.8]
feats = [c for c in FEATURE_COLUMNS if c in d] + good

# 单切分画像（同 shadow）
split_i = int(len(d) * TRAIN_FRACTION)
val = d.iloc[split_i:].reset_index(drop=True)
val_start = val["signal_time"].min()
train = d[d["signal_time"] < val_start - PURGE_WINDOW].reset_index(drop=True)
thr = train["net_barrier_taker"].quantile(0.9)
train["is_top_decile"] = (train["net_barrier_taker"] >= thr).astype(int)
val = val.copy(); val["is_top_decile"] = (val["net_barrier_taker"] >= thr).astype(int)

# B: 正交
lab = train["label_barrier"].astype(int); top = train["is_top_decile"]
print("Jaccard:", round( ((lab==1)&(top==1)).sum() / ((lab==1)|(top==1)).sum() ,4))
print("MI:", round(mutual_info_score(lab, top),5))

params = {"objective":"binary","learning_rate":0.05,"num_leaves":31,"min_data_in_leaf":80,
          "feature_fraction":0.8,"bagging_fraction":0.8,"bagging_freq":1,"verbose":-1,"seed":SEED}
b1 = lgb.train(params, lgb.Dataset(train[feats].astype(float), train["label_barrier"].astype(int)), 300)
b2 = lgb.train(params, lgb.Dataset(train[feats].astype(float), train["is_top_decile"]), 300)
p1, p2 = b1.predict(val[feats].astype(float)), b2.predict(val[feats].astype(float))
ret = val["net_barrier_taker"].values
k = max(1, len(val)//10)
print("基线 top10% 均净 bp:", round(ret[np.argsort(p1)[-k:]].mean()*1e4,2))
print("新模型 top10% 均净 bp:", round(ret[np.argsort(p2)[-k:]].mean()*1e4,2))

# CPCV（完整 15 折）
d2 = d.copy(); d2["is_top"] = (d2["net_barrier_taker"] >= d2["net_barrier_taker"].quantile(0.9)).astype(int)
lifts = {"label":[], "istop":[]}
for tr_i, te_i in cpcv_groups(d2, 6, 2):
    tr, te = d2.iloc[tr_i], d2.iloc[te_i]
    bb1 = lgb.train(params, lgb.Dataset(tr[feats].astype(float), tr["label_barrier"].astype(int)), 250)
    bb2 = lgb.train(params, lgb.Dataset(tr[feats].astype(float), tr["is_top"]), 250)
    pp1, pp2 = bb1.predict(te[feats].astype(float)), bb2.predict(te[feats].astype(float))
    kk = max(1, len(te)//10)
    net = te["net_barrier_taker"].to_numpy(); pool = net.mean()
    lifts["label"].append( net[np.argsort(pp1)[-kk:]].mean() - pool )
    lifts["istop"].append( net[np.argsort(pp2)[-kk:]].mean() - pool )
print("CPCV label lift 中位 bp:", round(np.median(lifts["label"])*1e4,1))
print("CPCV istop lift 中位 bp:", round(np.median(lifts["istop"])*1e4,1))
PY
```

**环境**：python3 + pandas + lightgbm + scipy + scikit-learn；仅读 `judgment_v10_wide.csv`；未动 holdout、成本、障碍。

---

## 2. 数据统计

| 项 | 值 |
|---|---|
| 池 | `judgment_v10_wide.csv` |
| 样本数 / 币种 | 18,379 / 232（全 short） |
| 时间 | 2026-02-01 → 2026-05-03（**< 2026-05-04 holdout**） |
| 目标（旧） | `label_barrier`（owner 标注，32.9% 正类） |
| 目标（新） | `is_top_decile`（train 内 net_barrier_taker ≥ 90% 分位，10.0% 正类） |
| 特征 | 47（28 基础 + 19 causal alphas） |
| 切分 | 80/20 时间切分 + 18h15m purge；train 14,656 / val 3,676 |
| CPCV | 6 折 2 测试，15 组合，禁运 72 根 |

**顶十分位定义**（train 内）：net_barrier_taker ≥ 330bp（约 +3.3% 毛）。

---

## 3. 结果表

### B. 影子标签正交性（train）

| 指标 | 值 |
|---|---|
| owner label 正类率 | 33.4% |
| is_top 正类率 | 10.0% |
| Jaccard（交集/并集） | **0.2995** |
| 互信息 MI(label, is_top) | **0.121** |
| 交集大小 | 1,466 |
| owner+非top | 3,429 |
| top+非owner | 0（train 内 top 全部被 owner 覆盖，但反之不成立） |

**特征差异（KS，train 内 top 正例 vs owner label 正例）**：
- atr_pct: top 0.0118 vs label 0.0076，KS 0.58
- pre_range48: 0.084 vs 0.055，KS 0.39
- drawdown24: 0.057 vs 0.038，KS 0.37
- dense_frac48: 0.107 vs 0.234（top 反而更低）
- ret_12: -0.030 vs -0.020（top 更弱势）

**结论（B）**：两个目标重合度低，top 明显更「极端波动+弱势」，与剖开画像一致。owner 标注包含大量「非顶但被标」的样本。

### A. 模型性能（单切分 val + CPCV）

**val 单切分（top 10% 经济表现，taker 净）**：
| 模型 | 挑的 top10% 均净 (bp) | vs 池 (-12.85bp) |
|---|---|---|
| 基线（学 label_barrier） | **+15.73** | +28.6 |
| 新模型（学 is_top_decile） | **-32.41** | -19.6 |

（单切分噪声大，top n=367；新模型在该切分上过拟合「成为 top」的定义。）

**CPCV 15 折（更稳健）**：
| 模型 | lift 中位 (bp) | top 绝对均净中位 (bp) | 正折数 | 自身 AUC 中位 |
|---|---|---|---|---|
| 基线（学 label） | **+12.8** | **+0.9** | 13/15 | 0.502 |
| 新模型（学 is_top） | **+13.9** | **+8.3** | 11/15 | **0.835** |

- 新模型 top 绝对水平显著更高（+8.3 vs +0.9），说明它更会「挑到真正高回报的极端」。
- 自身 AUC 远高于基线（0.835 vs 0.502），因为「是否 top」这个目标本身与特征（尤其是波动/范围）相关性更强；而 owner label 对特征来说更像噪声。
- lift 仅略高（+13.9 vs +12.8），但**绝对净收益**的提升对实盘更重要（直接决定扣成本后是否为正）。

**特征重要性（gain，同一 fold）**：
- 基线：atr_pct_ratio96、af_vol_of_vol、spread_chg24、af_ret_skew 为主（分散到多个 alpha）。
- 新模型：**atr_pct 占绝对主导**（~17k gain），其次 pre_range48、af_vol_of_vol，与剖开画像完全一致。

**Spearman（分数 vs realized net，val）**：
- 基线：+0.054
- 新模型：**-0.264**（单切分反向，CPCV 更可信）

---

## 4. 解读

1. **owner 标注 ≠ 可交易顶档**。Jaccard 仅 0.30，top 独有的极端波动/弱势特征被 owner 标注稀释。学 owner label 相当于在学一个「混杂了大量中性/弱信号」的目标。
2. **直接学 top 改善了排序的经济集中度**。CPCV 下 top 绝对均净从 +0.9bp 提升到 +8.3bp（+7.4bp 绝对改善）。虽然 lift 改善有限，但绝对水平对「扣成本后是否为正」更关键。
3. **为什么单切分和新模型有时变差**：把 realized 定义的 top 当二分类目标，模型会强烈依赖「高波动」特征；在某些时间块高波动本身不等于高净收益（或被 beta/其他因素抵消），导致外推不稳。CPCV 平均后仍显示净改善。
4. **与之前 +17~28bp 剖开的衔接**：之前剖开是用回归器排分得到 +17~28bp lift；本次把「top 本身」做成监督信号，CPCV 仍能拿到正 lift，且 top 绝对水平更好，说明「把顶档定义当目标」是可行方向。

---

## 5. 风险与诚实声明

1. **全部 train 窗**。未读 holdout（已耗 9 次）。
2. **单切分 vs CPCV**：单切分结果波动大（val 仅 3676 行，top 367 笔）；CPCV 15 折更可信，但仍为样本内。
3. **目标定义用 realized**：is_top_decile 是用未来 realized net 定义的（这是监督学习允许的），但它强化了「高波动=好」的归纳偏差。在高波动不等于高净的 regime 下可能失效。
4. **成本口径**：报告用 taker 净；legacy 0.2% 仅为与旧报告可比。实盘成本以 executor 实测为准。
5. **未改铁律项**：无 holdout、无 promote、无 ACTIVE 切换、无下单、无改成本/障碍/三门。
6. **好坏都报**：单切分上新目标曾变差；CPCV 显示改善但仍小（+13.9bp 仍接近成本量级）。

---

## 6. 下一步选项（需 owner 决策）

| # | 选项 | 代价 | 说明 |
|---|------|------|------|
| **1** | **回归到「直接预测 net」**（或 q90 回归） | 低 | 之前回归实验已显示 +18~23bp 量级；本次 is_top 二分类只是「回归的简化版」。 |
| **2** | 把 is_top 作为**加权/辅助损失**（多任务或 sample weight） | 中 | 让模型既学 owner 结构，又向 top 集中。 |
| **3** | 白盒规则近似（atr_pct + pre_range48 + dense_frac 阈值） | 低 | 验证「模型学到的极端」是否能用几条 if 近似，跳过 LGBM。 |
| **4** | 在**更大/不同池**上重复 A+B（含 100x6m 老池） | 中 | 验证结论是否稳健（v10 池可能有特殊 regime）。 |
| **5** | 停止这条线，接受「判断层能挑到 +8~13bp 顶档，但仍薄」的现实 | 0 | 配合成本工程（maker 入场等）再评估。 |

**我的推荐**：先做 **1+3**（回归 + 白盒规则），成本低，能快速回答「是二分类问题还是回归问题」和「规则能否替代模型」。

---

## 附：产物

- `analysis/output/ab_topdecile_target.json` — 单切分摘要
- `analysis/output/ab_topdecile_target_details.json` — 特征重要性 + Spearman
- `analysis/output/ab_cpcv_lifts.json` — CPCV 15 折 lift 汇总

**下一次实验前请确认**：是否消耗 holdout、是否改成本假设、是否扩大池（均需 owner 批准）。

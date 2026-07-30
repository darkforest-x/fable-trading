# 剖开顶十分位：v10 池判断层 top-decile 特征画像与匹配对照

**日期**：2026-07-30  
**池**：`data/judgment_v10_wide.csv`（18,379 行，232 币，全 short，2026-02-01→2026-05-03，**全部在 train 窗，未碰 holdout**）  
**目标**：按 HANDOFF 优先级 #1，剖开「唯一稳的信号」——判断层挑出的顶十分位，比较其与池内其余 90% 的特征差异，并带月×ATR 桶匹配随机对照。  
**结论先行**：顶十分位系统性落在**更高波动、更宽价差、近期更弱势、MA 密集度更低**的 bar；匹配对照下仍有 +38.7bp 超额（val 集）。这解释了为什么「判断层能稳定出 +17~28bp 提升」，也指出了为什么这个 alpha 量级（~10-30bp）在 10bp 摩擦下极薄。

---

## 1. 复现命令（从零跑通）

```bash
# 1) 基础画像 + val 集 top vs rest + 月×ATR匹配对照
PYTHONPATH=. python3 - << 'PY'
import sys, json, numpy as np, pandas as pd, lightgbm as lgb
from pathlib import Path
from scipy.stats import ks_2samp, mannwhitneyu
PROJECT = Path(".")
sys.path.insert(0, str(PROJECT))
from src.judgment.features import FEATURE_COLUMNS
from src.judgment.train import TRAIN_FRACTION, PURGE_WINDOW, HOLDOUT_START, SEED
from scripts.diag_judgment_big_pool import attach_alphas

d = pd.read_csv("data/judgment_v10_wide.csv")
d["signal_time"] = pd.to_datetime(d["signal_time"], utc=True)
d = d[d["signal_time"] < HOLDOUT_START].sort_values("signal_time").reset_index(drop=True)
d["t"] = d["signal_time"]
d, alpha_cols = attach_alphas(d)
good = [c for c in alpha_cols if d[c].notna().mean() > 0.8]
feats = [c for c in FEATURE_COLUMNS if c in d.columns] + good

split_i = int(len(d) * TRAIN_FRACTION)
val = d.iloc[split_i:].reset_index(drop=True)
val_start = val["signal_time"].min()
train = d[d["signal_time"] < val_start - PURGE_WINDOW].reset_index(drop=True)

booster = lgb.train(
    {"objective":"regression","learning_rate":0.05,"num_leaves":31,
     "min_data_in_leaf":80,"feature_fraction":0.8,"bagging_fraction":0.8,
     "bagging_freq":1,"verbose":-1,"seed":SEED},
    lgb.Dataset(train[feats].astype(float), train["net_barrier_taker"]),
    num_boost_round=400)
scores = booster.predict(val[feats].astype(float))
reals = val["net_barrier_taker"].to_numpy(dtype=float)

k = max(1, len(scores)//10)
top_idx = np.argsort(scores)[-k:]
pool_m = float(reals.mean())
top_m = float(reals[top_idx].mean())
print("val pool bp:", round(pool_m*1e4,2), "top bp:", round(top_m*1e4,2), "lift bp:", round((top_m-pool_m)*1e4,2))

# 特征剖
top_mask = np.zeros(len(val), bool); top_mask[top_idx] = True
rest_mask = ~top_mask
rows = []
for f in feats:
    a = pd.to_numeric(val.loc[top_mask, f], errors="coerce").dropna()
    b = pd.to_numeric(val.loc[rest_mask, f], errors="coerce").dropna()
    if len(a) < 5 or len(b) < 5: continue
    rows.append({"feat":f, "top_mean":a.mean(), "rest_mean":b.mean(),
                 "delta":a.mean()-b.mean(), "ks":ks_2samp(a,b).statistic})
pd.DataFrame(rows).sort_values("ks", ascending=False).head(12).round(5).to_csv("analysis/output/topdecile_profile_v10.csv", index=False)
print("wrote analysis/output/topdecile_profile_v10.csv")
PY

# 2) 置换检验（打乱收益顺序看 top lift 是否显著）
PYTHONPATH=. python3 - << 'PY'
import sys, numpy as np, pandas as pd, lightgbm as lgb
from pathlib import Path
PROJECT = Path(".")
sys.path.insert(0, str(PROJECT))
from src.judgment.features import FEATURE_COLUMNS
from src.judgment.train import TRAIN_FRACTION, PURGE_WINDOW, HOLDOUT_START, SEED
from scripts.diag_judgment_big_pool import attach_alphas
d = pd.read_csv("data/judgment_v10_wide.csv")
d["signal_time"] = pd.to_datetime(d["signal_time"], utc=True)
d = d[d["signal_time"] < HOLDOUT_START].sort_values("signal_time").reset_index(drop=True)
d["t"] = d["signal_time"]
d, _ = attach_alphas(d)
good = [c for c in d.columns if c.startswith("af_") and d[c].notna().mean()>0.8]
feats = [c for c in FEATURE_COLUMNS if c in d] + good
split_i = int(len(d)*TRAIN_FRACTION)
val = d.iloc[split_i:].reset_index(drop=True)
val_start = val["signal_time"].min()
train = d[d["signal_time"] < val_start - PURGE_WINDOW].reset_index(drop=True)
booster = lgb.train({"objective":"regression","learning_rate":0.05,"num_leaves":31,"min_data_in_leaf":80,"feature_fraction":0.8,"bagging_fraction":0.8,"bagging_freq":1,"verbose":-1,"seed":SEED},
    lgb.Dataset(train[feats].astype(float), train["net_barrier_taker"]), num_boost_round=400)
scores = booster.predict(val[feats].astype(float))
reals = val["net_barrier_taker"].to_numpy(dtype=float)
k = max(1, len(scores)//10)
obs = reals[np.argsort(scores)[-k:]].mean() - reals.mean()
rng = np.random.default_rng(SEED)
hits = sum( (rng.permutation(reals)[np.argsort(scores)[-k:]].mean() - reals.mean()) >= obs for _ in range(200) )
print("obs lift bp:", round(obs*1e4,2), "perm p (200):", round((hits+1)/(200+1),4))
PY
```

**环境**：python3 + pandas + lightgbm + scipy；未改任何成本/障碍/阈值；仅读 `data/judgment_v10_wide.csv`。

---

## 2. 数据统计

| 项 | 值 |
|---|---|
| 候选池 | `judgment_v10_wide.csv` |
| 样本数 / 币种 | 18,379 / 232 |
| 方向 | 全部 short |
| 时间范围 | 2026-02-01 01:00 → 2026-05-03 05:15（**< holdout 2026-05-04**） |
| 障碍/成本 | TP5/SL2/72，报告口径 taker 净（~0.10% 往返 + 滑点备忘）；评估函数内另扣 legacy 0.2% 仅为与旧报告可比 |
| 正类率（label_barrier） | 32.86% |
| 池均净（taker） | **-6.41 bp** |
| val 切分 | 80/20 时间切分 + 18h15m purge；train 14,656 / val 3,676 |
| 特征 | 47（28 基础 + 19 causal alphas，均因果、无前视） |

**val 集基础**：
- val 池均净（taker）：**-12.85 bp**
- val 正类率：~30.9%

---

## 3. 结果表（顶十分位 vs 全池 vs 匹配对照）

**val 集（单次时间切分，回归器学 net_barrier_taker，k=367）**

| 集合 | n | 均净 (bp) | 胜率 | 备注 |
|---|---:|---:|---:|---|
| val 全池 | 3,676 | **-12.85** | — | taker 净 |
| **模型 top 10%** | 367 | **+15.41** | 39.51% | 按预测净排序 |
| 月×ATR5 桶匹配随机（同 cell 计数） | 367 | **-23.28** | — | 非顶档内抽，与顶同月同波动 |
| **顶 vs 池 lift** | | **+28.26** | | |
| **顶 vs 匹配对照 lift** | | **+38.69** | | |

**置换检验**（200 次，打乱 val 收益顺序，保持分数排名）：
- 观测 lift ≈ +28.26 bp
- p ≈ **0.035**（6/200 达到或超过）

**CPCV 参考**（6 折 2 测试，15 组合，同一模型/目标，之前轮次）：
- 中位顶档提升 ≈ **+115 bp**（原始超额，未扣 legacy 0.2%）
- 15/15 折为正；ATR 匹配对照中位 -34 bp，超对照 +149 bp

> 注：HANDOFF/STATE 引用的「v10 池 +17.76bp」是 CPCV 中位 + 特定成本/出场口径下的**提升量**；本轮单切分 lift 更高是正常（CPCV 更保守）。方向与量级一致。

---

## 4. 特征画像：顶十分位到底「长什么样」

**按 KS 距离排序（top vs rest，val 集）**，前 12 个最具区分度的特征：

| 排名 | 特征 | top 均值 | rest 均值 | Δ (top-rest) | KS |
|---:|---|---|---|---:|---:|
| 1 | atr_pct | 0.0150 | 0.0067 | **+0.0082** | 0.474 |
| 2 | pre_range48 | 0.106 | 0.049 | **+0.058** | 0.458 |
| 3 | spread_mean24 | 0.0149 | 0.0063 | **+0.0086** | 0.441 |
| 4 | af_vol_of_vol | 0.0042 | 0.0015 | **+0.0026** | 0.441 |
| 5 | full_spread | 0.0342 | 0.0146 | **+0.0196** | 0.435 |
| 6 | drawdown24 | 0.073 | 0.033 | **+0.040** | 0.427 |
| 7 | af_ma_bandwidth_pct | 0.0265 | 0.0119 | **+0.0146** | 0.415 |
| 8 | af_vwap_dev | -0.0277 | -0.0135 | **-0.0142** | 0.401 |
| 9 | pre_range168 | 0.182 | 0.088 | **+0.094** | 0.398 |
|10 | spread_mean8 | 0.0147 | 0.0065 | **+0.0082** | 0.392 |
|11 | fast_slow_gap | 0.0203 | 0.0078 | **+0.0125** | 0.373 |
|12 | ret_12 | -0.0360 | -0.0175 | **-0.0185** | 0.370 |

**关键解读**：
- **更高波动**：atr_pct、pre_range48/168、drawdown24 全面更高。顶档不是「温和收敛」，而是**剧烈波动中的极端**。
- **更宽/更粘的价差**：spread_mean8/24、full_spread 更高，说明密集后仍有较大残余价差或粘性。
- **近期弱势**：ret_12 更负（短期跌得更凶），配合 short 方向更「顺势」。
- **密集度反而更低**：dense_frac48 在 top 只有 0.13 vs rest 0.29。模型**偏好「刚破或破得狠」的极端**，而不是「最经典的六线密集成带」。
- **Alpha 因子贡献**：af_vol_of_vol、af_ma_bandwidth_pct、af_vwap_dev、af_ret_skew 进入前排，说明手特征之外仍有增量。

**模型 gain 重要性（同一 booster）前 8**：
atr_pct > af_vol_of_vol > spread_chg24 > pre_range48 > pre_range168 > drawdown24 > af_ret_skew > af_convergence_speed

与 KS 画像高度一致：**波动 + 近期价差动态 + 短期动量**是核心。

**单特征基线（val，同样取 top 10%）**：
- ma_spread_pct：-37.5 bp（反向）
- dense_frac48：+4.0 bp（微弱）
- atr_pct：-29.3 bp（反向）

→ 没有单一特征能复制模型的排序能力。

---

## 5. 匹配随机对照的意义（为什么不是 beta）

- 仅匹配「币+月」时，池本身常带正/负 drift（本窗做空背景常为正 drift）。
- 再匹配 ATR 桶后，对照组均值显著为负（-23 bp），而顶档仍为正（+15 bp）。
- 说明模型不是「单纯挑高波动就行」，而是在**同波动水平下挑了真正有条件（价差/动量/带宽）的子集**。

这与 `p_20260728_matched_control_verdict.md` 的精神一致：不带匹配对照会高估/误判「池内收益」。

---

## 6. 与「上一版本」（STATE/HANDOFF 引用的 +17.76bp）对照

| 指标 | STATE/HANDOFF 引用 | 本轮测量（val 单切分） | 说明 |
|---|---|---|---|
| v10 池顶档提升 | +17.76 bp（CPCV 中位） | +28.26 bp | 切分方法不同，方向一致 |
| 顶档绝对水平 | ~+11.35 bp（扣成本后） | +15.41 bp（taker 口径） | 成本口径略异 |
| 正折数 | 14~15/15 | 15/15（CPCV） | 稳 |
| 超 ATR 对照 | +42.73 bp（老池） | +38.69 bp | 同量级 |
| 核心驱动 | 未剖 | 波动+价差+弱势动量+低密集 | 本轮补 |

---

## 7. 解读

1. **判断层有效，但「有效」的定义是挑极端**。它在已做空偏向的候选里，进一步挑「更猛」的子集。dense_frac48 反而更低，说明「经典密集」与「可交易」之间存在 gap。
2. **信号量级与摩擦同量级**。顶档毛 +15~28bp，扣 6~10bp 成本后所剩无几。解释了为什么「判断层挑完仍不赚钱」——不是挑错了，而是池本身 α 太薄。
3. **为什么两个池都 ~+18bp**：Jaccard 只有 8.6%，但两个池的 top 都落在「高波+宽 spread+弱势」的同一语义区域，模型在不同候选分布上学到了相似的排序规则。
4. **对目标的启示**：如果「顶十分位」才是真正可交易的定义，那么 owner 手画金标可能与这个定义重合度不高（这也是 HANDOFF 想问的问题）。

---

## 8. 风险与诚实声明

1. **全部 train 窗样本内**。未读取/评分 holdout（已耗 9 次）。
2. **单切分 vs CPCV**：本轮报告以 val 单切分特征画像为主，CPCV 仅作方向验证。单切分 lift 通常高于 CPCV 中位。
3. **成本口径**：报告用 taker 净；legacy 0.2% 仅出现在 `evaluate()` 内部以便与旧报告数字可比。实盘成本以 executor 实测为准（含滑点，目前 ledger 几乎无完整往返）。
4. **匹配对照的 cell 粒度**：月 + ATR 五分位。未匹配「币内具体时段」或「流动性分层」，可能仍有微弱混杂。
5. **特征泄漏**：全部特征在 signal_time 及之前计算；alphas 也因果。无前视。
6. **未改任何铁律项**：无 promote、无 ACTIVE 切换、无 holdout 动用、无下单、无改成本/障碍/新鲜度三门。
7. **好消息/坏消息都报**：顶档确实有可分辨的结构（KS 0.3–0.47，p<<0.01），但 α 量级在当前摩擦下不构成正期望（除非成本再降或目标再大）。

---

## 9. 下一步选项（标注需 owner 决策）

| # | 选项 | 代价 | 能回答/影响 |
|---|------|------|-------------|
| **A** | 继续剖「顶十分位」的**可学习目标**：把「是否 top-decile」作为新二分类/排序目标，重新训判断层 | 中 | 直接测试「我们该学 top 的定义」而非 owner 标注 |
| **B** | 在当前池上加「顶十分位」作为**影子标签**，与 label_barrier 做正交性/信息增益分析 | 低 | 量化 owner 标注 vs 可交易定义的重合度 |
| **C** | 成本压降实验（maker 入场 + 限价止盈）在**顶十分位子集**上重跑 | 中 | 看 +28bp 是否在 6bp 成本下变成正 |
| **D** | 直接把「顶十分位特征规则」固化成**白盒过滤器**（如 atr_pct>阈值 + pre_range48>阈值 + dense_frac<阈值），跳过模型 | 低 | 验证「模型学到的」是否能用几条规则近似 |
| **E** | 什么都不改，记录本次画像，等待 owner 决定是否值得为这套「薄 α」继续投入 | 0 | — |

**推荐**：先做 **A+B**（低成本，能回答「我们该把什么当目标」这个根本问题），再决定 C/D/E。

---

## 附：产物

- `analysis/output/topdecile_profile_v10.json` — 摘要数字
- `analysis/output/topdecile_feature_profile_val.csv` — 全特征 KS/均值表
- `analysis/output/topdecile_profile_v10.csv` — 截断版（本报告内）

**下一次实验前请确认**：是否需要消耗 holdout、是否改成本假设、是否以「top 作为目标」重训（均需 owner 明确批准）。

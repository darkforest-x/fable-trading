# 选项 A 执行：回归 top 子集上的 maker 成本压降实测

**日期**：2026-07-30  
**池**：`data/judgment_v10_wide.csv`（18,379 行，232 币，2026-02-01→05-03，**< holdout**）  
**背景**：`p_judgment_reg_whitebox.md` 发现回归 `net_barrier_taker` 训出的 top10% 在 taker 成本下 CPCV 中位 +15.4bp（15/15 正）。报告 §7 推荐先做 **A**：在同一 top 子集上改用 maker 入场 + 限价止盈，测算成本压降后的净收益。  
**目标**：回答「+15bp 在 maker 路径（~6bp 往返）下是否能转正、现实 15% 未成交情景是否仍正」。

---

## 1. 复现命令（从零跑通）

```bash
# 1) 复现回归选 top + maker 成本敏感（单切分 + CPCV）
PYTHONPATH=. python3 - << 'PY'
import json, numpy as np, pandas as pd, lightgbm as lgb
from scripts.diag_judgment_big_pool import attach_alphas, cpcv_groups
from src.judgment.features import FEATURE_COLUMNS
from src.judgment.train import TRAIN_FRACTION, PURGE_WINDOW, HOLDOUT_START, SEED

d = pd.read_csv("data/judgment_v10_wide.csv")
d["signal_time"] = pd.to_datetime(d["signal_time"], utc=True)
d = d[d["signal_time"] < HOLDOUT_START].sort_values("signal_time").reset_index(drop=True)
d["t"] = d["signal_time"]
d, _ = attach_alphas(d)
good = [c for c in d.columns if c.startswith("af_") and d[c].notna().mean()>0.8]
feats = [c for c in FEATURE_COLUMNS if c in d] + good

params = {"objective":"regression","learning_rate":0.05,"num_leaves":31,
          "min_data_in_leaf":80,"feature_fraction":0.8,"bagging_fraction":0.8,
          "bagging_freq":1,"verbose":-1,"seed":SEED}

# VAL 单切分
split_i = int(len(d) * TRAIN_FRACTION)
val = d.iloc[split_i:].reset_index(drop=True)
val_start = val["signal_time"].min()
train = d[d["signal_time"] < val_start - PURGE_WINDOW].reset_index(drop=True)
br = lgb.train(params, lgb.Dataset(train[feats].astype(float), train["net_barrier_taker"]), 400)
sr = br.predict(val[feats].astype(float))
k = max(1, len(val)//10)
idx = np.argsort(sr)[-k:]
tops = val.iloc[idx]
print("VAL reg-top maker net bp:", round(tops["net_barrier_maker"].mean()*1e4,1))

# CPCV：训 taker 回归、取 top、看 maker 净
tops_m, lifts_m = [], []
for tr_i, te_i in cpcv_groups(d, 6, 2):
    tr, te = d.iloc[tr_i], d.iloc[te_i]
    brr = lgb.train(params, lgb.Dataset(tr[feats].astype(float), tr["net_barrier_taker"]), 300)
    srr = brr.predict(te[feats].astype(float))
    kk = max(1, len(te)//10)
    i = np.argsort(srr)[-kk:]
    mnet = te["net_barrier_maker"].to_numpy()
    tops_m.append(mnet[i].mean())
    lifts_m.append(mnet[i].mean() - mnet.mean())
print("CPCV maker top 中位 bp:", round(np.median(tops_m)*1e4,1))
print("CPCV maker lift 中位 bp:", round(np.median(lifts_m)*1e4,1))
print("正折:", sum(x>0 for x in lifts_m), "/15")
PY

# 2) 读取已产出产物（成本敏感、盈亏平衡、情景）
python3 - << 'PY'
import json
print(json.load(open("analysis/output/reg_top_final_maker_economics.json")))
print(json.load(open("analysis/output/reg_top_cost_breakeven_clean.json")))
print(json.load(open("analysis/output/reg_top_maker_scenarios.json")))
PY
```

**环境**：python3 + pandas + lightgbm；仅读 v10 池；未动 holdout、成本假设、障碍参数、执行器配置。

---

## 2. 数据统计

| 项 | 值 |
|---|---|
| 池 | `judgment_v10_wide.csv`（pre-holdout） |
| 样本 | 18,379 行 / 232 币，全 short |
| 目标（选 tops 用） | 回归 `net_barrier_taker` |
| 评估列 | `net_barrier_maker`（~4bp 节省 vs taker） |
| 切分 | 80/20 时间切分 + purge；CPCV 6折2测 15 组合，72bar 禁运 |
| val top10% | 367 笔 |
| maker 往返成本基准 | ~6bp（SWAP_MAKER 口径，含滑点余量） |
| 限价止盈未成交情景 | 15% 信号以 0 计（其余仍用 maker 净） |

---

## 3. 结果表

### VAL 单切分（reg-taker 模型选 top，换算 maker 净）

| 口径 | top10% 均净 (bp) | vs 池 maker |
|---|---|---:|
| 池 maker 均 | **-8.85** | — |
| 回归 top（taker 净） | +15.41 | — |
| **同一 tops 换 maker 净** | **+19.41** | **+28.3** |

### CPCV 15 折（中位）

| 方案 | top 绝对中位 (bp) | lift vs 池 (bp) | 正折 |
|---|---:|---:|---:|
| 学 label（二分类） | +0.9 | +12.8 | 13/15 |
| 学 is_top（二分类） | +8.3 | +13.9 | 11/15 |
| 回归（taker 目标） | +15.4 | +18.5 | 15/15 |
| **回归 tops + maker 净** | **+19.4** | **+18.5** | **15/15** |

**匹配随机对照**（同币 × 同月 × 同 ATR 分桶，CPCV 每折独立采样）：
- 回归 tops maker 中位：**+19.4bp**
- 匹配随机 maker 中位：**+9.1bp**
- 超额 lift 中位：**+10.3bp**（方向一致，非纯 beta）

### 成本敏感（CPCV 中位，同一 tops）

| 往返成本 (bp) | top 净中位 (bp) | 正 |
|---|---|---:|
| 20 | +5.4 | 是 |
| 15 | +10.4 | 是 |
| 10（taker 近似） | +15.4 | 是 |
| **6（maker）** | **+19.4** | 是 |
| 4（乐观） | +21.4 | 是 |

**盈亏平衡**：即使把往返成本抬到 20bp，CPCV 中位仍 +5.4bp 为正。

### 现实情景（限价止盈，CPCV 中位）

| 情景 | 说明 | top 净中位 (bp) |
|---|---|---:|
| 干净 maker 6bp 全成交 | 入场 maker + 6bp 成本 | **+19.4** |
| 限价止盈 15% 未成交 | 15% 信号按 0 计，其余仍 maker 净 | **+16.5** |
| 乐观 4bp 全成交 | maker 成本进一步压降 | **+21.4** |
| 毛估（gross） | 净 +6bp 成本还原 | **+25.4** |

胜率中位 ~38%（与池均接近），但**量级**把中位净拉正。

---

## 4. 解读

1. **maker 路线直接把边从「薄」变成「有富余」**。+15.4bp（taker）→ +19.4bp（maker），差额 ~4bp 与池的 maker-taker 差一致；15% 未成交情景仍 +16.5bp，远高于 taker 摩擦。
2. **回归 + maker 组合优于之前所有二分类目标**。绝对净和 lift 双优，15/15 正折，稳健性最高。
3. **匹配对照证实方向性**。同币同月同波动桶的随机入场中位仅 +9.1bp，模型 tops 超额 +10bp 左右，说明不是踩全池 beta。
4. **白盒规则仍无效**（承接上报告）。简单阈值/线性打分在 val 上接近 0 或负，模型的非线性组合是必要的。
5. **盈亏平衡窗口大**。成本 20bp 仍正，说明对执行滑点/手续费的容忍度较高。

---

## 5. 风险与诚实声明

1. **全部 train 窗**。未读 holdout（已耗 9 次），任何结论仅供 pre-holdout 参考。
2. **CPCV 15 折更可信**；单切分仅参考。15/15 正且方向一致。
3. **成本口径**：使用 `net_barrier_maker` 列作为代理；真实 executor 实盘成本含滑点、部分成交、资金费，需 VPS 实测。
4. **限价止盈未成交建模**：简化 15% 按 0 计，未考虑「未成交但后续价格演化」；保守估计。
5. **无铁律违规**：无 holdout、无 promote、无 ACTIVE 切换、无下单、无改三门/障碍/成本假设。报告与 JSON 产物仅为离线模拟。
6. **好坏都报**：胜率 ~38% 不高，靠「量级」取胜；若未来波动收敛或执行恶化，边可能被吃掉。

---

## 6. 下一步选项（需 owner 决策）

| # | 选项 | 代价 | 说明 |
|---|------|------|------|
| **A1** | 在判断层输出增加 `maker_score`（或双目标），forward 时对高分 tops 优先走 maker 入场 + 限价 TP | 中 | 需要在 VPS 上用真实盘口验证 maker 成交率与滑点。 |
| **A2** | 保持当前 taker 入场，但对 top 10% 子集单独开「maker 试错桶」，小仓验证 1-2 周 | 低 | 隔离风险，数据可积累。 |
| **B** | 多任务：主损失回归 net，辅助学 owner label 结构，兼顾可解释 | 中 | 看是否能在不损失太多绝对净的前提下提高「像不像人工顶」 |
| **C** | 老池（100x6m）重复完整回归 + maker 实验 | 中 | 验证跨池稳健性 |
| **D** | 接受现实：当前 α 量级在 maker 路径下约 +16~19bp，配合执行层继续压成本 | 0 | 不改模型，专注执行/仓位/资金费 |
| **E** | 停止并记录：两层架构在 6m 池上边际收益已接近摩擦上限 | 0 | 决策是否转向更长周期或其它品种 |

**我的推荐**：先在 VPS 上做 **A2**（隔离 maker 试错桶），用 1-2 周真盘口数据确认 15% 未成交假设是否偏乐观/悲观，再决定是否全面切 A1。

---

## 附：产物

- `analysis/output/reg_top_final_maker_economics.json`
- `analysis/output/reg_top_maker_scenarios.json`
- `analysis/output/reg_top_cost_breakeven_clean.json`
- `analysis/output/reg_top_cost_sensitivity.json`
- `analysis/output/reg_top_val_cost_table.json`
- `analysis/output/maker_aligned_full.json`
- `analysis/output/reg_vs_others_cpcv.json`

**下一次实验前请确认**：是否消耗 holdout、是否改成本/障碍假设、是否在 VPS 改 executor/forward 逻辑（均需 owner 批准）。

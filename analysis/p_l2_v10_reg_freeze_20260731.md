# L2 切 v10 池回归 · 冻结与回测分析报告（2026-07-31）

> **范围**：owner 要求 L1=short_star_v10、L2= v10 候选池判断后，补写的冻结交付报告。  
> **未做**：holdout 终审、tip-replay 用**新** v10 判断重跑、真下单、改新鲜度门。  
> **对照终审（盘口）**：看板 `#backtest` tip-replay（v16 检测器权重，**不是**本冻结）；见 §6。

---

## 1. 一句话

在 **v10 检测候选池**（`judgment_v10_wide` → `judgment_yolo_swap_v10`）上训了回归 LightGBM，写入  
`models/frozen_tp5_sl2_swap_yolo_v10_reg_20260731` 并 **ACTIVE 切换**。  
**单切 val 顶十分位净 maker 约 +16bp**；**5 折 walkforward 并非全为正**（`all_net_positive=false`）。  
门控阈值 q90≈**−0.00044**（回归分，非旧 v11 的 0.0202）——**过门率约 91%**，门几乎不挡，经济上应看 **top-decile**，勿把「过门」当精选。

---

## 2. 复现命令

```bash
# 1) 从 wide 池生成冻结用表（data/ 不入 git，本机需有 judgment_v10_wide.csv）
PYTHONPATH=. python3 scripts/build_judgment_yolo_swap_v10.py

# 2) 冻结 + 写 ACTIVE（会改 models/ACTIVE；PREV=v11）
PYTHONPATH=. python3 scripts/freeze_model.py --yolo-v10-pool --write-active --date 20260731
```

产物：

| 文件 | 说明 |
|------|------|
| `data/judgment_yolo_swap_v10.csv` | 18,379 行；`realized_ret`=`net_barrier_taker`；`label`=`label_barrier` |
| `models/frozen_tp5_sl2_swap_yolo_v10_reg_20260731.txt` | LightGBM 模型 |
| `models/frozen_tp5_sl2_swap_yolo_v10_reg_20260731.json` | 元数据 + walkforward |
| `models/ACTIVE` | → 上述 `.txt` |
| `models/ACTIVE_PREV` | → `frozen_…_v11_reg_20260718.txt` |
| `models/SHADOW_V11_REG` | 回滚指针 |

回滚 L2 到 v11：

```bash
echo 'models/frozen_tp5_sl2_swap_yolo_v11_reg_20260718.txt' > models/ACTIVE
# 或: cp models/ACTIVE_PREV models/ACTIVE
# 并把 src/judgment/frozen.py default_config 改回 v11（若代码已切 v10 主线）
```

---

## 3. 数据统计

| 项 | 值 |
|----|-----|
| 源池 | `data/judgment_v10_wide.csv`（v10 检测器候选，全 short） |
| 冻结表 | `data/judgment_yolo_swap_v10.csv` |
| 样本数 | **18,379** |
| 时间（UTC） | **2026-02-01 → 2026-05-03**（全部 **&lt; holdout 2026-05-04**） |
| 目标 | 回归 `net_barrier_taker`（已含 taker 口径成本列；训练目标即该列） |
| 池均值 | ≈ **−6.4 bp** / 笔（源 wide 上 `net_barrier_taker`） |
| 正类 `label_barrier` 率 | ≈ **32.9%** |
| holdout | **未读、未评** |

时间切分（`load_splits`，purge 对齐 72bar）：

| 切分 | n | 时间窗（约） |
|------|---|--------------|
| train | 14,656 | 2026-02-01 → 2026-04-13 |
| val | 3,676 | 2026-04-14 → 2026-05-03 |
| holdout | 0（本池无 ≥05-04 行） | — |

---

## 4. 结果表

### 4.1 冻结元数据

| 项 | 值 |
|----|-----|
| config | `tp5_sl2_swap_yolo_v10_reg` |
| objective | regression |
| best_iteration | **1**（早停极早，需警惕） |
| **threshold_val_q90** | **−0.0004397** |
| 对照：旧 ACTIVE v11 thr | **0.02022** |

### 4.2 单切 train / val（冻结模型打分）

成本假设：maker 单边 0.02%→往返 **0.06%** 记入 `net_maker`；taker 往返 **0.10%** 记入 `net_taker`（与 `src/costs` 一致的数量级报告；训练目标已是 wide 的 net_barrier_taker）。

| 切分 | n | Spearman(score, ret) | 池均值 bp | **top-decile 毛 bp** | **top-decile 净 maker bp** | top 胜率 | 过 thr 比例 | 过 thr 净 maker bp |
|------|---|----------------------|-----------|----------------------|----------------------------|----------|-------------|---------------------|
| train | 14656 | 0.228 | −4.5 | +103.9 | **+97.9** | 46.4% | 88.5% | +0.1 |
| **val** | **3676** | **0.047** | **−12.9** | **+22.0** | **+16.0** | **43.9%** | **91.2%** | **−17.6** |

**解读**

- **有用信号在 top-decile**，不在「score≥q90」：q90 阈值落在负分附近，**~91% 过门**，过门子集 val 仍 **净 −17.6bp**。  
- val 顶十分位相对池 **lift ≈ +34.8bp**（顶均 − 池均）。  
- train 顶档夸张（+98bp maker）+ `best_iteration=1` → **过拟合 / 早停异常** 风险高，实盘勿当确认。

### 4.3 五折 walkforward（冻结脚本内置）

| 折 | val_start | n_val | spearman | top-decile 净 maker | 胜率 |
|----|-----------|-------|----------|---------------------|------|
| 1 | 2026-03-06 | 2206 | −0.018 | **−74.4 bp** | 26.4% |
| 2 | 2026-03-18 | 2205 | +0.095 | **+39.6 bp** | 55.9% |
| 3 | 2026-03-30 | 2206 | +0.159 | **+15.6 bp** | 37.7% |
| 4 | 2026-04-10 | 2205 | −0.042 | **+141.3 bp** | 51.4% |
| 5 | 2026-04-22 | 2206 | +0.021 | **−62.7 bp** | 48.6% |

| 汇总 | 值 |
|------|-----|
| rho_mean | 0.043 |
| rho_min | −0.042 |
| net_min（top-decile maker） | −74.4 bp |
| **all_folds_net_positive** | **false** |

---

## 5. 与上一版本（v11 ACTIVE）对照

| 项 | v11 reg（切前 ACTIVE） | **v10 reg（现 ACTIVE）** |
|----|------------------------|---------------------------|
| 池 | `judgment_yolo_swap_v11.csv` | `judgment_yolo_swap_v10.csv`（wide） |
| 检测叙事 | v11_chain 候选 | **short_star v10 候选** |
| thr (q90) | **0.0202** | **−0.00044** |
| 过门语义 | 较严 top 约 10% 量级（设计意图） | **极宽 ~90%**，门失效 |
| 本报告 val top 净 maker | （未在本轮重算） | **+16.0 bp** |
| walkforward 全正 | （历史 freeze 有 fold 表） | **否** |

**归因**：换池 + 目标列为 wide 的 `net_barrier_taker`；分数量级与 v11「预测 realized_ret」同哲学，但分布使 q90 几乎不起过滤作用。若生产要「稀缺开火」，需 **另定分位或绝对阈值**（单变量实验，owner 批）。

---

## 6. 看板「回测」页 · tip-replay（另一条线）

这是**检测层盘口终审**，**不是**本 L2 冻结的 holdout 回测：

| 项 | 值 |
|----|-----|
| 入口 | `http://127.0.0.1:8642/#backtest` · API `/api/backtest/tip_replay` |
| 源文件 | `analysis/output/v16_holdout_verdict.json` |
| 检测权重（历史跑批） | `models/owner_v16_tipuni_cold.pt`（**≠** 当前 L1 short_star_v10） |
| 窗 | holdout **2026-05-04 → 2026-07-16**（**已消耗 holdout 记录**） |
| 协议 | tip-replay：只见 bar≤t · 次根开 · TP5/SL2/72 · maker · A′ · MIN_GAP |
| 笔数 | **1206** |
| PF | **0.784**（&lt; 1.3） |
| 每笔净 | **−0.234%** |
| 胜率 | **29.4%** |

旧前视 p3 回测（PF≈6 / +245%）已归档：`analysis/archive/backtest_legacy_20260730/`，**不作裁决**。

---

## 7. 相关研究报告（v10 池判断，研究期）

| 报告 | 内容 |
|------|------|
| `analysis/p_judgment_topdecile_profile_v10.md` | 顶十分位画像 |
| `analysis/p_judgment_topdecile_target_ab.md` | 目标选择 A/B |
| `analysis/p_judgment_reg_whitebox.md` | 回归 + 白盒 |
| `analysis/p_judgment_maker_cost_on_regtop.md` | maker 成本 |
| `analysis/arch_overview_20260730.md` | 架构与 v10 池结论 |
| `analysis/evening_checklist_20260730.md` | 当晚需求对账 |

这些是 **研究诊断**，不是 ACTIVE 冻结交付；本文件才是 **L2 切换的冻结报告**。

---

## 8. 风险与诚实声明

1. **walkforward 不全为正** → 不能声称「稳定 alpha 已过门」。  
2. **best_iteration=1** → 模型几乎没训开，阈值与排序可能不稳。  
3. **q90 门过宽** → 与 v11「稀缺阀门」语义不一致；若直接用于 forward 打分，会大量过门。  
4. **训练目标 = net_barrier_taker**，与部分历史 freeze 的 realized_ret 定义可能差一个成本口径。  
5. **tip-replay 终审仍是 v16 权重 + 旧 holdout 跑批**，**未**用 short_star_v10 + 新 L2 重跑 holdout（禁止擅自再耗 holdout）。  
6. L1 仍是 **interim 非 tip-smoke 金标**；L1+L2 同标 v10 **不**等于系统已确认可实盘。

---

## 9. 下一步（需 owner 决策）

| 选项 | 说明 |
|------|------|
| A. 接受当前 ACTIVE 做纸面/探针 | 可；盯过门率与前向 |
| B. 单变量改门 | 例如 val q95/q99 或固定 top10% 动态门（需批） |
| C. 重训 | 修 early-stop / 更多 iter，再 freeze（单变量） |
| D. 回滚 v11 | `ACTIVE_PREV` / `SHADOW_V11_REG` |
| E. tip-replay 用现 L1+L2 重跑 | **holdout 再消耗**，必须 owner 明确批准并记 N |

---

## 10. 清单

- [x] 复现命令  
- [x] 数据统计  
- [x] 结果表 + 与 v11 对照  
- [x] val 顶档净收益 / 过门 / walkforward  
- [x] 解读与风险  
- [x] tip-replay 看板索引  
- [x] 下一步选项  

生成：`analysis/p_l2_v10_reg_freeze_20260731.md`

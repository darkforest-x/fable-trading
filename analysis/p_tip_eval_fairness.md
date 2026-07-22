# tip 验收公平性审计 — tip-smoke / tip_hit 会不会冤假错案？

**日期**：2026-07-23  
**纪律**：只读审计；未开训、未 promote、未耗 holdout。  
**触发**：Owner 质疑「tip 验收怎么验的？验收集是不是有问题？」

---

## 一句话答 Owner

两套验收测的**不是同一件事**；验收集/协议**确实有瑕疵**，但「v13/v14/v15 没解决盘口 tip」这条主结论**站得住**——冤假错案的主要方向是历史上**把 tip_hit 当实盘成功**（冤枉 acquittal），不是单纯「把好模型判死」。对 pad200 系，`tip_hit` 因 slice-MA 几何**可能偏严**，但 tip-smoke 与训图同 full-MA 仍 0 贴边，不能用「验收不公」翻案。

---

## 1. tip-smoke：逐步怎么测

脚本：`scripts/diag_forward_detect_lag.py --tip-smoke`  
入口：`scripts/eval_v15_vs_v12_tip.sh`（及 v12/v13/v14 同族）。

| 步 | 做什么 |
|---|---|
| 1 | 读 forward 快照（常用 `analysis/output/forward_log_vps_20260721.csv`） |
| 2 | 取账本里**不重复 symbol** → **27 币**（不是 27 个「已知 tip 事件」） |
| 3 | 每币加载本机/VPS `kline_fetched` 15m；**窗末 = 序列最后一根收盘 bar**（强制「当前 tip」） |
| 4 | 调 `scan_series_with_yolo(..., mode="tip")`：**只渲最右 200 窗** |
| 5 | 渲染 = `add_mas(全序列)` 再切窗 + `render_chart`（与实盘 live **同管线**） |
| 6 | conf 默认 **0.30**（`DEFAULT_CONF`）；eval 脚本未另设 `--tip-conf` |
| 7 | **A′ 贴边门参与**：`TIP_EDGE_BARS=2` → 只留 `bar_in_win ≥ 198` |
| 8 | 成功：`tipish_hits` 非空（命中 tip 或 tip−1）→ `fired`；汇总 `n_fired / 27` |

**27 币是谁**：BONK, CAP, DOOD, EDEN, GPS, HOME, KAITO, KGEN, KITE, KORU, LIGHT, MMT, MUU, MVLL, OL, OPG, PARTI, PEPE, PIEVERSE, RAM, RECALL, ROBO, SOON, SPX, UB, XPL, YB（均为 `*_USDT_SWAP`）。来源 = 该快照 forward_log 的 unique symbol，**不是**「此刻必有密集启动」的金标集。

对照：同脚本再跑 `mode=live` 作对照；历史结论 tip/live 常同为 0/27。

---

## 2. true_tip tip_hit：逐步怎么测

脚本：`scripts/tip_detectability.py --true-tip`  
数据：`datasets/dense_owner_v11` 的 **val 正样本**（limit 120）。

| 步 | 做什么 |
|---|---|
| 1 | 枚举 val 有框 stem（跳过已有 `_tip` 后缀图） |
| 2 | 按 stem 找回 K 线；用原窗索引定位金标框 |
| 3 | 取金标框**最右缘 bar** 为 signal；重切窗使 **窗末 = 该 bar**（200 根、无后文） |
| 4 | **`add_mas(切好的 200)`** ← **slice-MA**，与实盘/pad200 训练的 full-MA **不一致** |
| 5 | `render_chart` → YOLO `predict`，conf=**0.30** |
| 6 | 命中：任意预测框右缘归一化 ≥ **0.92**（≈ 贴右 8%） |
| 7 | 报 `tip_hits / n`（v12≈111/120；v14≈4/120；v15≈2/120） |

**金标从哪来**：v11 val 的人手/规则密集框（中段几何为主，右缘 p50≈0.5）。评测时再「裁到框右 = 窗末」——**是的，这就是「中段密集裁贴右」合成 tip 几何**，不是盘口当时正在启动的 tip。

与 v12 htip 训练同 slice-MA → tip_hit 对 v12 **友好**；对 pad200（full-MA 训）**偏严/不公**（见根因 A/B 8-stem 对照）。

---

## 3. 对照 Owner 直觉：「训框在右、实盘看最近 K」——验收测的是同一件事吗？

| | 训练想要的 | tip_hit | tip-smoke / 实盘 |
|---|---|---|---|
| 窗末语义 | 右缘=形态截止 / 无后文 | 金标框右缘（事后已知 cut） | **最新收盘 bar** |
| 是否保证「此刻有密集启动」 | 正样本有 | **有**（从金标裁出） | **不保证**（只是账本币当前 tip） |
| MA | v12=slice；pad200=full | **slice** | **full** |
| 贴边门 A′ | 训练无此门 | 无（只看右缘≥0.92） | **有（≥198）** |

- **几何意图**（框应贴右、无后文）：tip_hit / tip-smoke / pad200 **同向**。  
- **样本语义**（「现在盘口是不是一次密集启动」）：只有 tip-smoke≈实盘；tip_hit **不是**。  
- 所以 Owner 直觉对：**训「右缘形态」≠ 验「任意币此刻 tip」**；两套指标不能互替。

---

## 4. 验收集 / 协议可能的问题（真风险）

1. **协议鸿沟（H-DET-7，已证实）**  
   tip_hit 高 ≠ tip-smoke / tip_fire。v12：0.925 vs 0/27。用 tip_hit 宣称「能 tip」= **假阳性过关**。

2. **tip_hit = 中段裁贴右 + slice-MA**  
   - 对 pad200/v15：**MA 顺序不公**（训 full、评 slice）→ 可能把「live 其实还行」的模型在 tip_hit 上判死。  
   - 语义上仍是「已知密集事后裁右」，**不是**盘口 tip 出生率。

3. **tip-smoke 的 27 币不是正例集**  
   强制「当前 tip」多数时刻**本该无框**（tip-empty-ok）。绝对 0/27 **不能**单独证明「模型完全不会 tip」——也可能「这 27 个 tip 刚好都不是启动」。  
   缓解：同口径下 v12 低 conf 能出**中段**框却无贴边；账本 lag-walk tip_fire≈1/32；与「出生率≈0」同向。

4. **本机 K 线常停在冻结日**（如 smoke JSON 里 tip open_time≈2026-07-16）  
   与 VPS 当日窗不完全一致；**相对对照**（同快照比 v12/v14/v15）仍可用，**绝对时刻**勿当盘口实况。

5. **A′ 贴边门参与 tip-smoke**  
   会滤掉中段框（v12 典型）。这不是 bug：实盘入账同门。但若有人用「关掉 tip_edge 后的 raw 命中」当成功，会和发现级口径打架。A′ **不制造 tip**，只挡事后账。

6. **conf=0.30 固定**  
   TIP_CONF=0.22 已做过 smoke 对照，仍 0/27（H-DET-5）——阈值不是主冤案源。

7. **val mAP（尤其 tip-align val）**  
   绝不能当 tip 裁决；v15 mAP50≈0.72 与 tip 归零可并存。

---

## 5. 哪些结论仍站得住（即便验收有瑕疵）

| 结论 | 为何仍站 |
|---|---|
| **离线 tip_hit ≠ 盘口 tip** | v12 同权重双指标反差；H-DET-7 🟢 |
| **v13/v14/v15 未过 H-DET-1 发现级** | tip-smoke 同口径 0/27；且 v14 在**自家 pad200 训图**上能贴右开火 → 学到了训分布、**没迁到盘口 tip**（非「标签坏」） |
| **Hypothesis B 不够** | v15 把 val 也 tip-align 后 tip_hit 仍≈0，未回 v12 |
| **调度/降 conf 救不了 tip** | tip-only / TIP_CONF 双 0/27 |
| **A′ 不是 tip 解药** | 过滤≠产生 |
| **主线仍宜 v12，勿 promote pad200 系** | 相对 smoke 无改善 + tip_hit 崩；相对风险可控 |

**不会单凭 tip_hit 把「本可盘口开火」的 pad200 模型彻底判死**——因为 tip-smoke 与训图同为 full-MA，训图开火、smoke 仍 0，主因是**语义迁移失败**，不是 slice-MA 尺子单独造成的。

---

## 6. 若验收可疑：最小纠偏实验（需 Owner 批再跑）

**不开训、不 promote。** 只纠「尺子」：

| # | 实验 | 目的 | 规模 |
|---|---|---|---|
| **E1** | tip_hit 改 **full-MA** 重渲同 120 stem（H-DET-4） | 去掉对 pad200 的 MA 不公；看 v12/v14/v15 是否改排名 | CPU 小跑 |
| **E2** | 在 **Owner 已审的真实 tip 成败小样**上跑 tip-smoke 同门（正例/漏检/空背景分母分开报） | 把「27 币任意 tip」换成「有/无密集」条件概率 | 现有 `v13_real_tip_preview` + 目视 |
| **E3** | 同图：训图 pad200 / true_tip(slice) / live(full) 三协议 n≈20 叠框 | 机制对照，不替代主表 | 已有 8-stem 可扩 |

优先：**E2（真实 tip 金标分母）** > E1（尺子诚实）≫ 再同构 pad200 开训。

---

## 风险与诚实声明

- 本篇不新增实验数字；引用 `p_v14_failure_rootcause.md`、`p_v15_tip_val.md`、`diag_tip_smoke_v15.json`、`tip_rate_v15_tipval.json`。  
- tip-smoke 绝对率对「任意当前 tip」**偏苛**；tip_hit 对 v12 **偏松**、对 full-MA 训 **偏严**。  
- 「会不会把好模型判死」：对 **pad200 未迁移** 的判定——**不会仅因 tip_hit 冤死**；对 **「任意好 tip 模型」**——若只靠 27 币无条件 smoke，**有可能假阴性**，故需 E2 条件分母。

## 产物

- 本报告：`analysis/p_tip_eval_fairness.md`  
- 议程锚点：H-DET-3 / H-DET-7；learnings `pad200-train-fire-not-live-tip.md`

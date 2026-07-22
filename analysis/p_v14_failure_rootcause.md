# v14 tip 仍失败 — 根因分析（有证据）— 2026-07-22

**纪律**：未开新训、未 promote、未动 ACTIVE/frozen、未耗 holdout、未清 forward_log。

## 结论先行

**v14 不是「标签又坏了」**（MAD-on 抽检错窗≈0；存档 pad200 与 `process_pad200` 重渲 **MAD=0**）。  
也不是「模型完全没学到东西」——在**自家 pad200 训练图**上 conf=0.3 仍稳定贴右开火。  
失败是：**学到了 pad200 训练分布，但没有迁移到盘口 tip；同时 early-stop 用的中段 val 把 v12 的 true_tip 能力忘光了。**

| 指标 | v12 | v13（错窗） | v14（MAD-on） |
|---|---:|---:|---:|
| true_tip tip_hit（n=120, conf=0.3） | **0.925** | 0.008 | **0.033** |
| tip-smoke 贴边开火 | **0/27** | 0/27 | **0/27** |
| true_tip head：n_boxes=0 | 1/30 | **29/30** | **29/30** |
| 官方 val mAP50（辅，不可当 tip） | 0.534 | 0.027 | 0.155 |

**主线仍 v12。H-DET-1 发现级未过（v13+v14 双复验）。勿再同构 pad200。**

---

## 1. 数字对照（n_boxes / conf / 右缘 / 是否静默 0 框）

### 1.1 true_tip（`tip_rate_*.json`，val 金标重渲 tip，conf=0.3）

| | v12 | v13 | v14 |
|---|---:|---:|---:|
| tip_hits / n | 111/120 | 1/120 | 4/120 |
| details_head 零框 | **1/30** | **29/30** | **29/30** |
| head 内 tip_hit | 29/30 | 1/30 | 1/30 |
| head max_conf 中位 | ~0.41 | **0** | **0** |
| 有框时 conf 量级 | 0.32–0.69 | 单例 0.48 | 单例 0.45 |

→ v13/v14 在 true_tip 协议上是**静默不报框**，不是「报了但右缘偏了」。

### 1.2 tip-smoke（`diag_tip_smoke*.json`，账本 27 币强制当前 tip）

| | v12 | v13 | v14 |
|---|---:|---:|---:|
| tip 模式 n_fired | **0/27** | 0/27 | 0/27 |
| tip 模式 sum n_hits | 0 | 0 | 0 |
| live 模式偶发中段框 | （VPS 快照 0） | PIEVERSE 1 次非 tipish | 0 |

本机小样（conf=0.15，6 币当前 tip 窗，2026-07-16 冻结 K 线）：

| 权重 | 行为 |
|---|---|
| v12 | 常有**中段**框（bar_in_win≈88–183），**0 次** bar≥198 贴边 |
| v14 | 多数 **0 框**；偶发中段（如 GPS bar=143），**0 次**贴边 |

→ tip-smoke 失败对 v14 是「出生率≈0」；对 v12 更像「只画中段、被 tip_edge 挡掉」（见 §4）。

### 1.3 训练集几何（标签右缘）

| 集 | 正样本 | 框右缘 p50 | 右缘≥0.95（按正样本） |
|---|---:|---:|---:|
| v14 train pad200 | **2635** | **0.992** | **2635/2635（100%）** |
| v13 train pad200 | 3947 | 0.992 | 100%（含错窗） |
| v12 train htip | 8000 | 0.923 | ~51% |
| v14/v13/**v12 val** | 1509 正 | **0.509** | **~2%** |

v14 摘要：`mad_gate=true`，`val_policy=copy_orig_unchanged`，skip 1406（主因 `mad_fail_both_high`=1318）。

---

## 2. 训推是否一致？

| 环节 | 窗长 | 渲染器 | MA 算法 | 切点语义 |
|---|---|---|---|---|
| **pad200 训练图** | 200 | `render_chart` | **全序列 `add_mas` 再切** | 金标框右缘 = 窗末（crop-after-box） |
| **tip-smoke / 实盘 live** | 200 | 同 `render_chart` | **全序列 `add_mas` 再切** | 当前最新收盘 bar = 窗末 |
| **true_tip 评测** | 200 | 同 `render_chart` | **先切 200 再 `add_mas`** | 金标框右缘 = 窗末 |
| **v12 tip 克隆（htip）** | 200 | 同 | **先切再 MA**（与 true_tip 同） | 同 true_tip |

**硬证据**：

1. `process_pad200` 新鲜重渲 vs 存档 `*_pad200.png` → **MAD=0.0000**（同 stem `0G_USDT_SWAP_002130`）。训推像素管线自洽，不是渲坏。  
2. v14 在 **5/5 自家 pad200 训练图**上 tipish 开火（conf≈0.37–0.65，右缘≈0.99）；v12 在同图上 **0/5 框**。  
3. 同金标 tip 切点、**8 stem 小样**：

| 协议 | v12 tip | v14 tip |
|---|---:|---:|
| A = true_tip（slice→MA） | **7/8** | **0/8** |
| B = live（full→MA） | **0/8**（有框 3/8 但非贴边） | **0/8**（有框 1/8 非贴边） |

**判读**：

- 训练图 ↔ tip-smoke：**同一渲染器 + 同一全序列 MA** → **不是「渲成两套图」那种粗暴不一致。**  
- true_tip ↔ pad200/v14 训练：**MA 顺序不一致** → 解释 v14 的 tip_hit 崩、也解释 v12「tip_hit 高但 live 贴边低」。  
- tip-smoke 的窗末是「账本币的当前 tip」，**不必**等于历史上某条密集金标的 cut → 语义差才是 tip-smoke 的主因。

---

## 3. early-stop 用的 val 是否学偏？

**是，且有结构性证据。**

| 事实 | 证据 |
|---|---|
| val **故意未 pad** | `pad200_summary.json` → `val_policy=copy_orig_unchanged` |
| val 几何 = v11 中段金标 | 右缘 p50=0.509，≥0.95 仅 ~2% |
| best 按官方 val fitness | v14 best=**ep16**（patience=10，ep26 stop）；val mAP50=**0.155** |
| 底座本有 tip 能力 | v12 tip_hit **0.925** → finetune 后 **0.033** |

纯贴右 train（100% 右缘≥0.95）+ 中段 val early-stop → 选出的权重在 true_tip 上接近「不报框」，却仍能拟合 pad200 训练图。  
**B 不能单独解释 tip-smoke**（v12 同 val 尺子但 tip_hit 高），但能解释 **v14 相对 v12 的 catastrophic forgetting**。

---

## 4. v12 为何 tip_hit=0.925 而 tip-smoke 也是 0/27？

这是 **H-DET-7 协议鸿沟**，两个指标测的不是同一件事：

| | true_tip tip_hit | tip-smoke |
|---|---|---|
| 样本从哪来 | val **已知密集金标**，窗末=框右缘 | forward_log **27 币当前 tip**（不必有密集启动） |
| MA | 切窗后再算（= htip 克隆） | 全序列再切（= 实盘） |
| 成功定义 | 任意框右缘 ≥0.92（归一化） | tip/tip−1 有信号 **且** `TIP_EDGE_BARS=2`（bar≥198） |
| v12 表现 | **111/120** | **0/27**（本机 conf0.15：有中段框、无贴边） |

一句话：**true_tip 问的是「金标密集事件裁成 tip 几何后还认不认」；tip-smoke 问的是「这些币现在盘口 tip 有没有贴边开火」。**  
v12 答对了前者、答错了后者——所以离线 tip_hit 不能当实盘 tip 解药（已登记 H-DET-7 🟢）。  
v14 **两者都挂**：连前者也忘了，后者仍 0。

---

## 5. 归因排序（带证据权重）

| 秩 | 假设 | 权重 | 证据 | 反证/边界 |
|---|---|---|---|---|
| **1** | **C pad200 正样本语义 ≠ 盘口 tip** | **高** | 训图上开火；tip-smoke 贴边仍 0；金标 crop-after-box ≠「当前 tip 正在启动」；H-DET-1 发现级双复验失败 | 标签错窗已排除（v14 MAD-on） |
| **2** | **B 中段 val early-stop 学偏** | **高** | val 未 pad；tip_hit 0.925→0.033；best 跟 val mAP | 单独不解释 tip-smoke（v12 同 val） |
| **3** | **A 训推协议不一致** | **中（对 true_tip）/ 低（对 tip-smoke）** | true_tip=slice-MA vs 训练/实盘=full-MA；8 stem A/B 对照；v12 在 B 上 tip=0/8 | pad200↔smoke 同 full-MA + MAD=0，**不是 tip-smoke 主因** |
| **4** | **D 样本少 / 只学贴右空壳** | **中低** | 正样本 2635≪v12 的 8000 混合；右缘 100% 贴右 | tip-smoke 上 v14 **并不**乱贴右空壳（多数 0 框），更像过拟合训分布、泛化失败 |
| **5** | **E 其它** | **低** | v13 错窗已修仍失败；conf0.15 仍无贴边；非 tip_edge「滤掉了好框」 | — |

**不是主因**：再修一轮标签、再训同构 pad200、用 val mAP↑ 宣称 tip 进步。

---

## 6. 下一步（唯一建议）

**停 pad200 同构重训。下一步唯一建议：收「真实 tip 成败」金标小样并目视定标（`analysis/p_v13_real_tip_collect_plan.md`），用盘口 tip 语义替换「中段金标裁右缘」。**

- 需要 owner 点头：扩采规模 / 是否开训。  
- 排队但不抢主线：H-DET-4（把 true_tip 口径改成 full-MA，消融渲染差）——先对齐指标诚实，不直接制造 tip。  
- **禁止**：promote v14、自动切 ACTIVE、耗 holdout、清 forward_log、用 val mAP 反驳 tip 失败。

---

## 风险与诚实声明

- tip-smoke 用 `forward_log_vps_20260721.csv` 同口径快照；本机 K 线停在 07-16，与 VPS 当日不完全同窗，但 v12/v13/v14 对照一致且与既有 0/27 结论同向。  
- 小样推理 n=5–8，用于机制对照，不替代 n=120 / n=27 主表。  
- tip_hit 0.033（4/120）相对 0.008 的抬升可能是噪声。  
- 未跑 frozen-F1 / holdout。

## 产物索引

- 主表：`analysis/output/tip_rate_v{12,13,14*}.json`，`diag_tip_smoke*.json`  
- 数据：`datasets/dense_owner_v14_pad200/pad200_summary.json`  
- 终局：`analysis/p_v14_pad200_train.md` · 议程：`docs/RESEARCH_AGENDA_DETECT.md` H-DET-1  
- 本报告：`analysis/p_v14_failure_rootcause.md`

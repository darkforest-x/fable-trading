# Owner 标框手法 → 因果特征 → train base rate 裁决 — 2026-07-23

**纪律**：纯离线，`<2026-05-04`（**未碰 holdout**）；TP5/SL2/72bar；成本同时报
swap maker 0.06%（与 emergence 对照）与 legacy 0.2%。不 promote、不改 ACTIVE、不改
新鲜度门。

回答 owner 的张力：audit 说信号薄（emergence PF≈0.87），但手动「完美密集」开单有感觉。
能不能用 10000+ 标框图做特征工程 + LightGBM 披露手法，再按因果原则用历史 base rate 裁判？

## 复现命令

```bash
PYTHONPATH=. .venv/bin/python scripts/owner_label_feature_verdict.py \
  --hard-neg --n-symbols 0 --tag owner_label_feature_verdict
# 输出:
#   analysis/output/owner_label_feature_verdict.json
#   analysis/output/owner_label_feature_verdict_feature_gain.csv
```

## A. 张力与 v17 值不值得赌（先读证据）

| 证据 | 结论 |
|---|---|
| v16 holdout#6 | 纯检测 PF 0.78；v11 判断反预测（top5% PF 0.48） |
| emergence base rate | 规则密集 tip PF **0.874** @ maker；毛已略负；稳定略胜随机 |
| same-source 判断 | walk-forward 不稳健；edge 随行情 regime 摆动 |
| 本轮 owner 标框 | **oracle 选点 PF 1.183**；**可部署因果规则 PF 0.869≈emergence** |

**张力怎么裁**：两边都对，但说的不是同一件事。

1. Audit 量的是**因果可部署**的「盘口密集刚够格 / tip」→ 薄，PF~0.87。
2. Owner 手感来自**看完整段密集→启动后再框**的选点（oracle）→ train 上确有增量
   （PF 1.18 vs 0.87），但右缘时刻 **只有 1.7% 仍满足密集阈值**，框中位在窗宽
   **50%**（不是 tip），LGBM 第一特征是 **`spread_chg8` 扩大**（启动已在打印）。
3. 因此：手感 alpha ≈「确认启动后的选点」，不是「盘口 tip 出生」；把后者训成 v17
   **不会自动继承**前者的 1.18。

**v17 tip 金标赌不赌**：

- **不该赌「v17 会救出 1.18」**——1.18 来自事后确认语义；真 tip 金标若标对，经济学更接近
  emergence / v16，而不是 owner 中段框。
- **可以小成本继续攒真实 tip 分布**（采集引擎已在跑），目的是诚实测量盘口可检性，
  **不是** pe 赌可交易线。
- 若 owner 实盘手感其实是「看到散开/启动确认再下」——那是另一条策略，应用因果
  「确认规则」另测，而不是 YOLO tip 检测器。

## B. 数据与 API

| 集 | 图/框 | 本轮角色 |
|---|---:|---|
| `datasets/_deprecated_pretip/dense_owner_v11` | 11730 图 / **5831** 正框 | **主源**（owner 原标） |
| `dense_owner_v12_htip` | ~10k 框 | tip clone 膨胀，含重复 |
| `data/golden_pool.json` | 6229 框 / 12567 stem 键 | 窗口消歧存档 |
| 存活 tip 集 v14/v15/v16 | 较少 | 训练禁用非盘口语义；本轮不拿来当手法金标 |

复用：`build_crop_pad200` 的 `resolve_win_start` + `boxes_cut_and_spans`；
`add_indicators` + `add_features`；障碍与 `base_rate_dense_offline` 同构（TP5/SL2）。

MAD 失败跳过 1870；holdout 切点丢 311；有效 cut **3318** 框 · **233** 币 ·
时间 2025-06-05 → 2026-05-03（train 段）。

## C. 结果

### 1) 手法披露（LGBM gain，不当裁判）

| 特征 | gain | 读法 |
|---|---:|---|
| **spread_chg8** | 28368 | 标框时刻 spread **在变大**（启动中） |
| fast_slow_gap | 6826 | 快慢锚仍相对近 |
| spread_mean24 | 3968 | 近窗均 spread 低于随机 |
| spread_pos96 | 3930 | 处在自身 96 窗偏高位（已从最紧散开） |
| … | | ret_*/ext_up 亦进前十 |

- val AUC **0.954**（时间切分）——**反常偏高 → 第一假设 hindsight 标签时机**，非特征泄漏。
  已排除框几何进 LGBM（负样本无框，否则 AUC=1.0 假象）。
- 标框右缘仍密集比例：**1.66%**。
- 框几何中位：宽 12 bar，高 1.36%价幅，**右缘在窗内 50.8%**（中段，不是 tip）。
- hard-neg 对照 in-sample AUC=1.0（仅诊断，过拟合，不作数）。

### 2) 因果规则（top4 AND，可解释阈值）

```
spread_chg8     >= 0.00383
fast_slow_gap   <= 0.01068
spread_mean24   <= 0.00688
spread_pos96    >= 0.487
```

对 owner 正样本召回 ≈ 规则覆盖（见 JSON `rule_recall_on_owner_pos`）。
规则本质接近「曾经紧 + **正在散开**」——确认态，不是 tip 态。

### 3) Train 段 base rate（裁决）

| 口径 | n | 胜率 | PF @ maker 0.06% | PF @ 0.20% | 单笔净 @ maker |
|---|---:|---:|---:|---:|---:|
| **Emergence（已发表）** | 16262 | 29.5% | **0.874** | — | -0.00085 |
| **Owner 标框时刻（oracle）** | 3112 | 35.0% | **1.183** | **1.039** | +0.00183 |
| **因果规则全市场扫描** | 33118 | 29.7% | **0.869** | 0.736 | -0.00112 |

## 裁决句

**手法相对 emergence：oracle 选点有增量（PF +0.31 @ maker），但可因果部署的特征规则无增量（PF≈0.87）。**
增量绑定在「事后确认启动」的选点上，不能翻译成 tip/盘口密集过滤器；扣 0.2% 后 oracle
也仅 PF 1.04，距可交易线 1.3 仍远。**只信因果 base rate，不信 LGBM AUC。**

## 对 v17 / A / B / C 的建议（需 owner 决策）

| 选项 | 建议 | 理由 |
|---|---|---|
| **v17 tip 金标** | **低优先级旁路**，勿当救命主线 | 真 tip ≠ owner 中段框；不继承 PF 1.18 |
| **A maker-on-holdout** | 仅当要定论「规则+旧判断 maker 过不过 1.3」时再耗第 7 次 | 与本轮手法结论正交；本轮未授权不跑 |
| **B 成本工程** | 若接受「薄利」：oracle 在 maker 下 train 略正 | 实盘需确认进场是 tip 还是确认态；否则期望回到 0.87 |
| **C 收摊 / 换命题** | **若坚持「盘口 tip 可交易」——证据支持收摊或换数据源** | 因果可部署边从未稳健过 1.3 |

**本执行者倾向**：把「手动完美密集」记为**确认态 oracle 边**（有、但薄、难因果化）；
**停止**用 YOLO tip 去追这个手感；要么显式做「确认散开」规则小实验（单变量），要么 C。

## 风险与诚实声明

1. **框右缘事后标** → AUC 0.95 / oracle PF 1.18 都可能含「启动已打印」确认偏置；
   因果规则一落地增量消失，与此一致。
2. **随机负样本** ≠ 差一点的密集；gain 夸大「像不像标框」。hard-neg 仅诊断。
3. **MAD 丢弃 1870** 框；存活 3318 可能偏向存档图与 kline 仍对齐的币。
4. 规则阈值来自正样本分位，**train 内轻度自拟合**；发现级，非 holdout 终审。
5. **未消耗 holdout**；不得把 PF 1.18 写成可上线证据。
6. 已知 MA/窗索引等 bug：本脚本走 MAD + 全序列 `add_mas`/`add_indicators`，与
   `base_rate_dense_offline` 同测量族；**未**半小时改全仓 bug。

## 产物

- 脚本：`scripts/owner_label_feature_verdict.py`
- 数值：`analysis/output/owner_label_feature_verdict.json`
- gain 表：`analysis/output/owner_label_feature_verdict_feature_gain.csv`
- learning：`docs/learnings/owner-label-oracle-alpha-is-not-causal-tip-alpha.md`

# 未来优化 backlog（现在不做）

**日期**：2026-07-22（同日补：判断层详写 + 判断层专搜 GitHub）  
**用途**：把近期讨论过、**此刻不落地**的优化项记清楚，避免下次会话「又从头聊一遍」。  
**纪律**：不改生产默认、不打断 v13 训练、不自动 promote、不清 forward_log。

---

## 一句话先说清楚

**当前瓶颈在检测层 tip 出生率≈0**（盘口无后文窗几乎点不着火）。  
判断层 / 执行 / 风控里多数「看起来很香」的优化，要等 tip 稳定开火后再做——否则是在优化一个几乎吃不到的子集（见 tip 子集折扣 ~0.05）。

**诚实声明（关于 GitHub）**：

- 上次开源大搜（`p_github_optimize_candidates.md`）**偏检测层**（FiftyOne / ONNX / ChartScan…）。  
- **本次专搜判断层**：公开仓库里几乎没有「加密 + YOLO 检测 + LightGBM 判断」现成两层方案；能抄的是**通用积木**——概率校准、组合风控规格、回测↔实盘一致性思路。  
- 详见下文 **「B4. 判断层 · GitHub 可借鉴」**。

**相关报告**：

- 检测侧：`p_github_optimize_candidates.md`、`p_realtime_yolo_within_bar.md`、`p_chartscanai_review.md`、`p_tip_only_smoke.md`
- 判断侧：`p_tip_subset_val.md`、`p_weight_centric_val.md`、`p_exit_parity.md`、`p2b_h13_btc_regime.md`、`week_plan_20260720.md`

**文档里已写的「研究课题 / 研究方向」**（本 backlog 不另造一套；细节以议程为准）：

| 文件 | 写了什么 |
|------|----------|
| [`docs/RESEARCH_AGENDA.md`](../docs/RESEARCH_AGENDA.md) | **主入口**：研究议程（进化引擎）；H1–H19 + H-TIP；发现级/确认级；优先队列 |
| [`PROJECT_PLAN.md`](../PROJECT_PLAN.md) | 两层定稿：2a YOLO + 2b LightGBM；triple-barrier；2b 验收标准 |
| [`docs/DOC_MAP.md`](../docs/DOC_MAP.md) | 活文档索引：`RESEARCH_AGENDA` = 假设状态表 |
| [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) | 现行：检测→判断（回归排序）→冻结→前向 |
| [`docs/archive/NEXT_STEPS.md`](../docs/archive/NEXT_STEPS.md) | 历史指针 → 研究假设队列 |
| [`docs/archive/SESSION_LOG_2026-07-07_09.md`](../docs/archive/SESSION_LOG_2026-07-07_09.md) | 07-09：议程首版 18 假设 + 两级验证 |

判断层假设簇：**出场** H1–H6 · **多 TF/趋势** H7–H9 · **方向/宇宙** H10–H12 · **特征质量** H13–H19。实盘期确认级靠前向，勿 val 挖矿。

**议程与实盘并行关系（2026-07-22）**：`RESEARCH_AGENDA` 的 H1–H19 发现级大多已结；当前主线是 **H-TIP 系（v12→v13）+ 前向 100 确认级**，不是再按旧表 H9→H10→H1 开工。实盘运维（贴边过滤、tiered、三门）与 tip 训练并行；判断层 H* 确认/新发现级要等 tip 通了再回议程队列。

---

## 时间桶怎么读

| 桶 | 含义 |
|----|------|
| **现在不做** | 有结论或会分心；开关可留，默认关 |
| **等 tip 通了 / 等 v13 后** | tip_fire 稳定 >0，或 v13 过 owner 目视后再立项 |
| **更远** | 前向新鲜样本够多，或硬件/产品条件具备 |

---

## A. 检测层（2a / YOLO / tip）— 摘要

详细表格仍以检测几何/加速为主（上次已评）。要点：

| 桶 | 项 | 一句话 |
|----|-----|--------|
| 现在不做 | 永久 tip-only / 默认 RIGHT_BIAS / 降 TIP_CONF 当解药 | tip-smoke 已证伪抬 tip_fire |
| 现在不做 | ChartScanAI 权重 | **仅警示**；不接主线 |
| 现在不做 | bar 内未收盘 / TensorRT·DeepStream | 不治出生率；要 tip>0 + 硬件证据 |
| 等 v13 | tip-smoke 复测、真实 tip 成败扩采、是否切主线 | **切主线需 owner** |
| 等 tip | FiftyOne / CVAT / FDAL / ONNX / OpenVINO | 策展或加速，不抬 tip_fire |

完整检测行表见历史版本段落结构；本轮重点扩 **B**。

---

## B. 判断层 / 执行与风控（详写）

> 实盘能吃到的近似 tip 可检子集。val 上 tip_strict 净收益 / 全量净 ≈ **0.0465**（`p_tip_subset_val.md`）。  
> **通 tip 前别指望复现 accept / 全量回测的漂亮数字。** 确认级仍是前向新鲜 100 笔，不是 val PF。

### B0. 口径前提：tip 子集折扣 + 前向 100

| 字段 | 内容 |
|------|------|
| **是什么问题** | 主线 val/accept 漂亮数字大量来自「扫描窗中部、事后才看得见」的盒子；live tip + 30min 门只能吃到约 **5%** 量级的群体净收益（折扣~0.05）。把全量 PF 当实盘预期会高估约 **20×**。 |
| **为什么现在先不做「拧 2b 追全量」** | tip_fire≈0 时，再校准、再加特征、再熔断，优化的是几乎空集合。 |
| **怎么做（本仓已有）** | `scripts/tip_subset_backtest.py`；报告 `analysis/p_tip_subset_val.md`；看板/周报应写 tip 子集口径，不写全量净当实盘。前向：`data/forward_log.csv` + 新鲜度三门 30min；确认级 = 新鲜 100 笔。 |
| **需 owner？** | 改成功标准叙事/成本假设时 **是**。 |
| **耗 holdout？** | tip 子集 val 实验未碰 holdout；**前向 100 不是 holdout**。 |

---

### B1. 现在不做（判断层）

#### 1）v12/v13 候选池重建 + 判断重冻 + accept（holdout#6）

| 字段 | 内容 |
|------|------|
| **是什么问题** | 检测主线已是 v12，判断层仍冻在 **v11 池**（`frozen_tp5_sl2_swap_yolo_v11_reg_20260718`）。检测换了、判断候选分布没换 → 分数/阈值可能错位；要「真·同池」需重建 judgment 数据集、重训/重冻、accept。 |
| **为什么现在先不做** | (1) tip 未通，重建池也喂不饱实盘；(2) accept 级动作会触发 **holdout 第 6 次消耗**；(3) v13 pad200 还在训，过早重建可能白做。 |
| **怎么做（本仓已有）** | `src/judgment/build_dataset.py`（输出文件名带池名）→ `src/judgment/train.py`（不加 `--eval-holdout`）→ `scripts/freeze_model.py` → cutover 脚本如 `scripts/cutover_v12_after_rescan.sh`；对照 `analysis/week_plan_20260720.md` 里「v12 池 + holdout#6」项；报告须记「第 N 次消耗 holdout」。 |
| **需 owner？** | **是**（立项 + 批准耗 holdout + 是否 promote ACTIVE）。 |
| **耗 holdout？** | **是**（accept/终审配置；当前账本目标为 **#6**）。发现级 val-only 可先跑，但最终验收必批。 |

#### 2）isotonic（及同构「校准分→仓位」）再试一轮

| 字段 | 内容 |
|------|------|
| **是什么问题** | 想把 LGBM 排序分变成「概率」再乘仓位；直觉好听。 |
| **为什么现在先不做** | `p_weight_centric_val.md` + learning：isotonic 把分压成**台阶**，阈值附近交易被静默弃单，输给 plain tiered。同构方案禁止「再碰运气」。 |
| **怎么做（若将来换假设）** | 已有对照：`scripts/weight_centric_backtest.py`；学习笔记 `docs/learnings/isotonic-sizing-collapses-rank-scores-to-steps.md`。若试 **Platt/beta** 等，必须单变量、train 窗拟合、val 看是否仍弃单——见 B4。 |
| **需 owner？** | **是**（新仓位实验）。 |
| **耗 holdout？** | **否**（发现级）；上生产仓位公式才另批。 |

#### 3）判断层大特征包 / 多变量打包

| 字段 | 内容 |
|------|------|
| **是什么问题** | 一次塞 dominance + funding + 新技术因子，想「一次到位」。 |
| **为什么现在先不做** | 违单变量纪律；tip 未通时信号噪声主导，归因不能。 |
| **怎么做** | `src/judgment/features.py` 单特征 PR；先例打包须 PROJECT_PLAN 记录 + owner 批（2b-v2 先例）。 |
| **需 owner？** | 打包时 **是**；单变量实验立项也建议点头。 |
| **耗 holdout？** | 训练/调参 **否**；最终验收才 **是**。 |

#### 4）动 holdout「看一眼」

| 字段 | 内容 |
|------|------|
| **是什么问题** | 想确认新池/新特征在验收窗的手感。 |
| **为什么现在先不做** | 铁律 1：看一眼 = 消耗一次；#6 留给池 cutover 终审。 |
| **怎么做** | `train.py` **不加** `--eval-holdout` 即安全；要评必须对话书面批准 + 报告记账。 |
| **需 owner？** | **是**。 |
| **耗 holdout？** | 一旦评就是 **是**。 |

---

### B2. 等 tip 通了 / 等 v13 后

#### 5）分数 → 仓位深化（tiered 已上线一部分）

| 字段 | 内容 |
|------|------|
| **是什么问题** | 高分该不该加仓；如何与保证金/权益对齐。 |
| **为什么暂缓深化** | 口径① tiered 已上 VPS；q99+ val 样本少；**前向分层 PF 还没数据**（tip≈0）。isotonic 路线已证伪。 |
| **怎么做（本仓已有）** | `src/judgment/frozen.py` 的 `SizingTiers`；sidecar `sizing_tiers`；`scripts/weight_centric_backtest.py` 对照；learnings：`tier-multiplier-needs-margin-headroom-in-base-notional.md`。改档/改公式先 shadow 再真仓。 |
| **需 owner？** | **是**（改档、改 unit 公式、提杠杆/充值）。 |
| **耗 holdout？** | **否**。 |

#### 6）regime 特征（资金费率已有；BTC dominance 等）

| 字段 | 内容 |
|------|------|
| **是什么问题** | 大盘状态可能调节密集启动胜率；想加全局特征。 |
| **为什么暂缓进 ACTIVE** | H13 BTC regime 斜率类增益≈噪声（`p2b_h13_btc_regime.md`）；dominance 须单变量立项；tip 未通时看不到实盘交互。 |
| **怎么做（本仓已有）** | 资金费率：`src/data/fetch_funding.py`（已有，勿重复造轮）。dominance 草稿：`pycoingecko`（见 B4）→ CSV → `features.py` 一列 → `train.py` val-only。脚本参考 `scripts/h13_btc_regime.py`。 |
| **需 owner？** | 进 2b / ACTIVE **是**；纯离线草稿可先写不进主线。 |
| **耗 holdout？** | 发现级 **否**。 |

#### 7）特征卫生 / 无前视复查 + 单特征基线

| 字段 | 内容 |
|------|------|
| **是什么问题** | 新列是否偷看未来；新池上 LGBM 是否只是赢了单特征噪声。 |
| **为什么不「现在大扫」** | tip 优先；但 **改特征表时**卫生不是可选项。 |
| **怎么做** | `features.py` docstring 写清列与窗口；时间切分 + purge；报告必报单特征基线（质量标准）。池重建同批重跑基线。 |
| **需 owner？** | 改特征表建议知会；**不**自动耗 holdout。 |
| **耗 holdout？** | **否**（直到验收）。 |

#### 8）成本口径（看板 maker vs 实盘 taker/滑点）

| 字段 | 内容 |
|------|------|
| **是什么问题** | 纸面常用 maker 0.06%/往返更低成本；实盘 tip 急单可能 taker + 滑点 → 纸面美化。 |
| **为什么暂缓改成功标准** | tip 无成交时改口径只改叙事；先记债。 |
| **怎么做** | `src/backtest/maker_val_sim.py`；weight_centric / tip_subset 已有 0.2%/0.3% 对照；成交后用 forward_log 实测往返成本表，再决定看板默认假设。 |
| **需 owner？** | **是**（改成功标准成本假设）。 |
| **耗 holdout？** | **否**。 |

#### 9）仓位口径与回测「10 槽」对齐

| 字段 | 内容 |
|------|------|
| **是什么问题** | 回测常按 ~10 并发单位仿真；实盘 `max_concurrent=1` + tiered → 收益曲线形状不同，不能直接比 PF。 |
| **为什么暂缓** | tip 通前没有「真实槽位占用」序列。 |
| **怎么做** | `maker_val_sim` / weight_centric 的并发帽参数；executor 配置对照；统一叙事或改仿真帽到 1（单变量）。 |
| **需 owner？** | **是**（改仿真帽或提高实盘并发）。 |
| **耗 holdout？** | **否**。 |

---

### B3. 更远（判断 / 执行）

#### 10）单一出场实现重构

| 字段 | 内容 |
|------|------|
| **是什么问题** | 回测与前向各有一套出场路径；已靠**显式传参**做到 parity（`p_exit_parity.md`），但不是「一份代码」。 |
| **为什么暂缓** | 等价已测；重构有回归风险；tip 未通时改出场 ROI 低。 |
| **怎么做** | `src/judgment/forward_scan.py` 的 `resolve_forward_exit*`；`tests/test_exit_parity.py`；learning：`exit-parity-holds-only-via-explicit-call-args.md`。抽共享函数后必须绿测再动 TP/SL。 |
| **需 owner？** | 动 TP/SL/障碍语义 **是**；纯重构知会即可。 |
| **耗 holdout？** | **否**（除非顺带验收新障碍）。 |

#### 11）组合熔断（回撤 / 连亏）

| 字段 | 内容 |
|------|------|
| **是什么问题** | 已有 `executor_KILL`、tiered、max_concurrent=1；缺「日损% / 连亏 N / 回撤超阈 → 自动冷却」产品规则。 |
| **为什么暂缓** | 真金规则；样本太少时阈值无法校准；易误杀唯一 tip。 |
| **怎么做** | **只抄规格** 自 Freqtrade Protections（见 B4）：MaxDrawdown / StoplossGuard / CooldownPeriod → 映射到本仓 touch KILL 或临时拒开。禁止 `pip install freqtrade` 进主依赖（GPL）。 |
| **需 owner？** | **是**（真金，逐次授权）。 |
| **耗 holdout？** | **否**。 |

#### 12）回测↔前向一致性（Basana 思路，不换框）

| 字段 | 内容 |
|------|------|
| **是什么问题** | 标签回放、入场代理、成本扣法与 live executor 边界是否漂移。 |
| **为什么暂缓** | 出场 parity 已做；整框换 Basana 成本高且无 YOLO 层。 |
| **怎么做** | 审计清单：信号时序、entry 代理 vs merge 回填、费用、并发帽；对照 Basana「策略同路径、exchange 可替换」思路（B4）。 |
| **需 owner？** | 改执行边界 **是**。 |
| **耗 holdout？** | **否**。 |

---

### B4. 判断层 · GitHub 可借鉴（2026-07-22 专搜）

**搜了什么**：概率校准、组合风险/熔断、回测↔实盘一致、特征/时间切分工具；并复查是否存在 OKX+YOLO+LGBM 两层整仓。  
**没找到什么**：**没有**现成「OKX + YOLO 检测 + LightGBM 判断（+ 新鲜度三门）」整仓可换——公开物是通用积木或 chart-YOLO demo；**不能**指望 GitHub 治 tip 供给。检测向候选见 `p_github_optimize_candidates.md`，此处不重复排期。  
**纪律**：不打断 v13；GPL/AGPL **不进主依赖**；isotonic→仓位同构已证伪（B1 / `p_weight_centric_val.md`），校准项勿复读失败路径。

| # | 积木 | 地址 | 干嘛 | 借鉴什么（人话） | 何时做 | 许可证注意 |
|---|------|------|------|------------------|--------|------------|
| 1 | **概率校准（现有 sklearn，不新装重库）** | [scikit-learn/scikit-learn](https://github.com/scikit-learn/scikit-learn) → [`CalibratedClassifierCV`](https://github.com/scikit-learn/scikit-learn/blob/main/sklearn/calibration.py)；LGBM 实践线索 [lightgbm#1562](https://github.com/microsoft/LightGBM/issues/1562) | 把排序分拧成「更像概率」的数，方便读盘/分层，不是换模型 | **怎么用现有栈**：① 本仓已有 `sklearn`；② 时间切分 train→fit LGBM 后，用**未参与调参的校准窗** `CalibratedClassifierCV(est, method="sigmoid", cv="prefit").fit(X_cal, y_cal)`（Platt）；③ 或 `IsotonicRegression`（`weight_centric_backtest.py` 已走过）；④ **校准与仓位映射拆开评估**——禁止把校准 P 再乘成连续仓位同构（isotonic 台阶化已证伪）。可选对照：[betacal/python](https://github.com/betacal/python)（MIT，★少，仅 val-only 实验） | tip 通 + 有前向/校准样本后；现在只读书 | sklearn **BSD-3**（已在依赖，零增量）；betacal 不进生产默认 |
| 2 | **组合风险：Freqtrade Protections（只抄思路）** | [freqtrade/freqtrade](https://github.com/freqtrade/freqtrade) → `freqtrade/plugins/protections/`（`max_drawdown_protection` / `stoploss_guard` / `cooldown_period` / `low_profit_pairs`）；文档 [Protections](https://www.freqtrade.io/en/stable/plugins/) | 连亏、回撤、冷却：何时停手、歇多久 | **只抄规格清单** → 映射本仓 touch KILL / 临时拒开；对照已有 KILL+tiered+max_concurrent，缺的是「日损% / 连亏 N」**产品阈值**。**禁止** `pip install freqtrade` 或粘 GPL 源码 | 前向新鲜 ≥50–100；规格草案可先写 | **GPL-3.0** → **不引依赖、不 copy-paste**；自写门槛即可 |
| 3 | **组合优化（可选）vs 更轻自写** | [dcajasn/Riskfolio-Lib](https://github.com/dcajasn/Riskfolio-Lib)；报表指标可选 [quantopian/empyrical](https://github.com/quantopian/empyrical) / empyrical-reloaded | 多资产权重、CVaR、风险平价；或 maxDD/Sharpe 报表 | 本仓是**稀疏 tip + 低并发**，不是全市场再平衡——Riskfolio **ROI 低**（还拖 CVXPY）。优先自写敞口/同币冷却/日损熔断；empyrical 类最多作看板指标，或 20 行自写回撤 | **更远**；默认自写 | Riskfolio **BSD-3**（可引但重）；empyrical **Apache-2.0**（原版维护停） |
| 4 | **回测↔实盘一致：Basana（思路）** | [gbeced/basana](https://github.com/gbeced/basana) | async 事件驱动；回测 exchange 与 live 尽量同一策略路径（偏加密） | **抄边界清单**：信号时序、entry 代理 vs merge、费用、并发帽；对照 forward_log vs executor。**不**整框替换（OKX tip、三门、tiered 是本仓专有） | 前向 100 后再做一致性审计 | LICENSE 正文 **Apache-2.0**（GitHub SPDX 偶显 Other——以文件为准）；建议思路级，勿整仓迁入 |
| 5 | **回测引擎对照（仅思路）** | [polakowo/vectorbt](https://github.com/polakowo/vectorbt)；[kernc/backtesting.py](https://github.com/kernc/backtesting.py) | 向量化 / 轻量策略回测，快速扫参数与成本假设 | 对照「成本、滑点、并发槽」叙事是否自洽；**不**换掉本仓障碍标签回测。成功标准仍是 top-decile 净收益 + 置换 p | tip 有成交、要对齐成本/槽位口径时当检查清单 | vectorbt：**Apache-2.0 + Commons Clause**（商用/转售受限）；backtesting.py：**AGPL-3.0** → **禁止引入**，只读文档 |
| 6 | **特征/时间切分（轻量、少污染）** | 首选已有 sklearn [`TimeSeriesSplit`](https://github.com/scikit-learn/scikit-learn/blob/main/sklearn/model_selection/_split.py)（`gap=`）；可选 [eslazarev/purged-cross-validation](https://github.com/eslazarev/purged-cross-validation)（`purgedcv`：PurgedKFold / embargo / CPCV） | 防标签重叠泄漏的 CV；本仓判断训练已有双边 purge（`p2b_judgment_audit.md`） | **默认不新装**：卫生复查用现有时间切分 + docstring。若要多折敏感性 / CPCV 路径再评估 `purgedcv`（sklearn 协议友好）。**不要**为「看起来更 AFML」把主训练改随机折 | 池重建 / 新特征 PR 前做卫生；CPCV 属更远诊断 | sklearn **BSD-3**（已有）；purgedcv **MIT**（可引非必须，星少先读） |

**结构提醒（非新仓）**：公开 meta-labeling 案例（如 [gautierpetit/meta-labeling-alpha-filter](https://github.com/gautierpetit/meta-labeling-alpha-filter)）说明「一级出侧、二级估值不值」——本仓 2a/2b **已经是**该结构；缺的是 tip 供给，不是再叠第三层元模型。

**补充（判断相关、上次已提）**：`pycoingecko`（MIT）→ BTC dominance 草稿；资金费率本仓已有。仍须单变量 + owner 立项进 2b。

**推荐排序（判断层 GitHub）**：

1. 先把 **tip 子集口径**写进预期（零依赖）。  
2. tip 通后：sklearn **Platt** 对照（避开已失败的 isotonic→仓位）；Freqtrade **熔断规格草案**（不装包）。  
3. 再远：Basana 一致性审计；自写/empyrical 类指标进报表；必要时读 `purgedcv`。  
4. 默认否决：Riskfolio/vectorbt/backtesting.py/Freqtrade **进生产或整框**；再叠一层 meta-labeling 赶时髦。

---

## C. 推荐再开聊的顺序

```
v13 训完 → tip-smoke / 成败预览（owner 目视）
    → tip 供给是否起来
        → 是：池重建+holdout#6（owner）→ 成本/槽位对齐 → Platt/仓位深化 → 熔断规格
        → 否：继续检测几何/打标；判断层只做口径提醒与特征卫生，别空转拧收益
```

**禁止顺手做**：脉冲塞实验；自动 promote；清 forward_log；耗 holdout 偷看；ChartScan 权重；AGPL/GPL 回测框进主依赖。

---

## 风险与诚实声明

- 本 backlog **不是**排期承诺。  
- 「等 tip」不是永久拖延特征卫生；**改特征表时**仍要做无前视复查。  
- 判断层 GitHub 专搜结论：**积木有、整机方案无**（B4）；上次大搜偏检测属实，本轮已补校准/风控/回测一致/时间切分。  
- holdout#6 与「现在不做池重建」并存：下次想重建必须先问 owner 并记账。

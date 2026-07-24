# fable-trading 项目管理计划（2026-07-24）

> **权威状态**：实时只认 `HANDOFF.md` 顶部。  
> **历史路线图**：`PROJECT_PLAN.md`（三阶段已走完，按历史阅读）。  
> **作战细节**：short 链路见 `analysis/p_short_only_pipeline.md`；待办见 `analysis/todo_short_only_pipeline.md`。  
> **纪律**：`AGENTS.md` / `CLAUDE.md` 铁律 + 实盘纪律 7–12。

---

## 0. 一句话目标

用 **盘口 tip 检测 + 方向分模判断 + 带成本回测/前向** 验证「双均线密集启动」是否可交易；  
确认级只认 **前向新鲜 100 笔**，不认 val AUC / 自家 mAP / 旧 frozen-F1。

**当前主战场（Owner 已定）**：先跑通 **short-only 全链路**，再谈 long / 双链路并行上线。

---

## 1. 项目结构（四层，不是三阶段旧叙事）

| 层 | 职责 | 当前正式状态 | 当前研究状态 |
|---|---|---|---|
| **L0 数据/管道** | K 线、脉冲、新鲜度、执行账本 | VPS 写者；三门 30min；脉冲 <15min | 继续守纪律，不塞实验进脉冲 |
| **L1 检测** | 盘口 tip 视觉候选 | **live `detector=none`** | short **`owner_side_short_tip_v1b`**（未 promote；**不是 v17**） |
| **L2 判断** | 候选 → 收益排序/筛单 | **ACTIVE = v11_reg frozen**（long 池时代产物） | short 回归对齐 v11：`p2b_yolo_short_30_6m_reg`（发现级） |
| **L3 确认** | 前向 100 笔新鲜裁决 | **0/100** | 无 tip 入账 → 无法启动确认时钟 |

旧长边栈（v12–v16 检测 + v11 判断接 tip）在 holdout#6/#7 已大面积证伪；**不得**把旧 accept PF 当现役可交易证明。

---

## 2. 成功标准（全项目统一）

### 2.1 发现级（可继续扩样 / 迭代）

- 时间切分 val；**无 holdout**
- 主指标：top-decile（或 val-q90）扣 **0.2%** 往返后 **净收益 > 0**
- 辅指标：Spearman(score, realized_ret)、置换诊断 p（不单独决胜）
- 检测辅证：tip-smoke / 真 tip 协议（val mAP **永不**作晋升裁决）

### 2.2 稳健级（才允许讨论影子/晋升申请）

- 同池 **walkforward**：net_mean > 0，且负折频率/幅度可接受（默认目标：≥4/5 折净≥0，或负折可解释且不系统性反害）
- 样本厚度：top 分位 n 明显厚于小样本伪影区（short 经验：n=24 级不可信；≥150 起步谈，≥500 更稳）
- 单特征基线对照仍优于噪声

### 2.3 确认级（才允许谈实盘切换）

- **前向新鲜 100 笔**（时钟不清空、不调门刷数）
- 扣真实成本假设后经济指标达标（具体阈值 **Owner 另批**；历史阶段 3 曾用 PF≥1.3 / 回撤≤20% 作参考，**不自动沿用**）
- 检测器晋升门 = **真 tip 金标 + tip-smoke**（纪律 12）

### 2.4 明确非目标

- 不以 AUC>0.6、val mAP、旧 frozen-F1、规则池数字冒充 tip 主链结论
- 不自动 promote；不在 holdout 上调参

---

## 3. 工作流与阶段闸门（2026-07-24 起）

```text
Phase S0  纠偏与基线（已完成）
   ↓
Phase S1  short 检测可用（tip_v1b 发现级）—— 已完成训练+smoke；未晋升
   ↓
Phase S2  short 判断扩样与稳健性 —— 【当前】
   ↓
Phase S3  检测金标加固（1000 目视 / 真 tip）—— 待 Owner
   ↓
Phase S4  障碍/阈值/成本 Owner 批 + 可选 holdout —— 待 Owner
   ↓
Phase S5  影子 / 有限 live 接 tip —— 待稳健级+Owner
   ↓
Phase S6  前向 100 确认 + 是否切换 ACTIVE —— 待确认级
```

### Phase S0 — 纠偏与基线（✅ 完成）

- 坏 short v1 叫停（非 tip 框 + 非时间切分）
- tip 金标重建 + 时间切分
- 主线哲学：short = **YOLO tip → 回归 LGBM → 分位筛单**；镜像默认输入
- binary / top-K 支线关闭

**退出物**：报告 + 可复现 CLI（`--side short --objective regression`）

### Phase S1 — short 检测发现级（✅ 完成训练评估 / ⬜ 未晋升）

| 项 | 状态 |
|---|---|
| 权重 | `owner_side_short_tip_v1b/best.pt` |
| tip-smoke | 19/27 tip · 4/27 live |
| 晋升 | **NO** |

**退出标准（晋升申请）**：Owner 目视/金标门通过；默认先做 S3 的 1000 框，不急 promote。

### Phase S2 — short 判断扩样与稳健性（🔄 进行中）

| 里程碑 | 状态 | 退出标准 |
|---|---|---|
| S2.1 5×6m 首表 | ✅ | 发现级可读（历史） |
| S2.2 30×6m 回归 | ✅ | 单切净 +0.371%；ρ=0.15 |
| S2.3 30×6m walkforward | ✅ | net_mean +0.336%；**间歇正边** → 未达稳健级 |
| S2.4 100×6m 扫池 | 🔄 ~70%+ | CSV 完整、币名单可复现 |
| S2.5 100×6m 回归单切 | ⬜ | 报告：净/ρ/q90/基线/风险节 |
| S2.6 100×6m walkforward（建议） | ⬜ | 与 30 池同表对照 |

**S2 决策树**

1. **稳住**（单切正 + walkforward 明显好于 30 池）→ 进入 S3/S4 讨论  
2. **仍间歇**（均值略正、折间大起大落）→ **停扩样本叙事**，回到 S3 检测金标/信号定义  
3. **转负/随机** → 记录证伪；禁止靠特征截断硬救；Owner 选换命题或收摊

**禁止**：binary 再优化、改 TP/SL/成本、holdout#8、promote。

### Phase S3 — 检测金标加固（⬜ 待 Owner）

Owner 已点名事项：

- 用 tip_v1b 在**实际 K 线**上检出约 **1000** 框  
- **排除**已用于 `dense_owner_side_short*` 的样本  
- 产出可审阅包（脚本 `dump_short_tip_detect_sample.py` 若未落地则先补薄脚本）

可选并行（长边数据债，不抢 short 主线 CPU 叙事）：

- 真实 tip 采集引擎继续攒样 → 未来 **v17**（盘口分布首训）；**v17 ≠ tip_v1b**

### Phase S4 — 参数与 holdout（⬜ 仅 Owner 批）

每次只开一项（单变量）：

| 决策包 | 内容 | 默认建议 |
|---|---|---|
| D-障碍 | trend/MA/trail 或 TP/SL 倍数 | 先沿用现网默认；改则单变量 |
| D-阈值 | val-q 分位 / 成本 0.2% | 不改成本假设除非 Owner |
| D-holdout | #8 是否消耗 | **默认不做**；仅稳健级后书面批 |

### Phase S5 — 影子 / 有限接入（⬜）

前置：S2 稳健级 + S1/S3 检测门至少一项 Owner 认可。

- 影子写独立日志，**拒写**主 `forward_log` 业务路径（防呆）  
- 新鲜度三门同值；不改脉冲预算塞扫描  
- 真下单 / 改仓 / kill / API key：**仅 Owner 亲手或逐次授权**

### Phase S6 — 确认与切换（⬜）

- 前向 100 笔达标 → 才讨论 ACTIVE / frozen 切换  
- 切换模板参考历史 v10→v11 cutover；**必须**记账 holdout 次数与 promote 批准原文

---

## 4. 角色与 RACI（简表）

| 事项 | Owner | Agent/执行 |
|---|---|---|
| 主线哲学（回归 vs binary、short-only） | A/C | R（实现） |
| promote / ACTIVE / 清 forward_log | A | 禁止擅自 |
| holdout 动用 | A（对话明确批 + 报告记账） | R（跑一次评估） |
| 阈值/TP·SL/成本 | A | R（单变量实验） |
| 扫池/训练/报告/walkforward | C | R |
| 真下单/改仓/API | A | 禁止 |
| learnings 笔记 | C | R（非平凡问题后） |

A=Approve · R=Responsible · C=Consulted

---

## 5. 节奏与交付物

### 5.1 日常节奏

| 节奏 | 内容 |
|---|---|
| 每个实验单元 | 单变量；`analysis/p_*.md` 含复现命令/表/风险/下一步 |
| 每个长任务 | 更新 `HANDOFF.md` 顶部「当前真相」 |
| 非平凡结论 | `docs/learnings/*.md`（extract-approach） |
| 代码落盘 | 源码+报告+指标 JSON；排除 lock/pid/进行中 log/`data/` |

### 5.2 本周（07-24 → 07-27）优先队列

1. **P0** 完成 100×6m 扫池 → 回归单切报告（+ 建议 walkforward）  
2. **P1** 刷新 `todo_short_only_pipeline.md` / HANDOFF 顶部  
3. **P2** Owner 决策：是否启动 1000 目视框包  
4. **P3** 仅当 S2 稳住：讨论障碍包或 holdout#8（书面批）  
5. **旁路** §7-2 / v17 采集：不杀、不抢主线、不自动训

### 5.3 报告最低质量条（可检查）

- [ ] 复现命令  
- [ ] 数据统计（n / 正类率 / 时间窗 / val n）  
- [ ] 与上一版本同表对照  
- [ ] 必报：净收益、胜率、ρ 或置换、单特征基线  
- [ ] 解读归因  
- [ ] 风险与诚实声明  
- [ ] 下一步（标注哪些需 Owner）

---

## 6. 风险登记（当前）

| ID | 风险 | 等级 | 缓解 |
|---|---|---|---|
| R1 | 小样本/币选择伪影（5 币正净在 30 币 binary 消失） | 高 | 扩样 + walkforward；禁止叙事回退 AUC |
| R2 | short 回归「间歇正边」 | 高 | 100 池复验；不稳则回检测金标 |
| R3 | 检测器未晋升 + live 无 tip → 确认时钟停摆 | 高 | 诚实空转；S3 金标；不装假检测器 |
| R4 | holdout 已耗 N=7，再烧污染终审 | 高 | 默认冻结 #8；书面批才动 |
| R5 | 脉冲塞实验破坏 tip 新鲜度 | 中 | 预算 <15min；实验离线跑 |
| R6 | 把 tip_v1b 误称为 v17 / 自动 promote | 中 | 命名与 ACTIVE 分离；本计划写死 |
| R7 | 长边旧栈数字污染 short 结论 | 中 | 分边表；主表 short-only |

---

## 7. 红线（违反 = 返工）

1. holdout 未批就评估或调参  
2. 随机切分 / 特征前视  
3. YOLO 开 fliplr/mosaic/mixup/hsv 等  
4. 自动 promote / 清 forward_log / 改 ACTIVE  
5. 改新鲜度三门不同值  
6. 脉冲内塞扫描或新重任务  
7. 真金操作无 Owner 逐次授权  
8. 非盘口事后路径冒充 live 检测  
9. 多变量打包改动未批  
10. 只报好消息、无风险节  

---

## 8. 决策日志模板（追加用）

```text
日期：
决策人：Owner
议题：
选项：
选择：
影响阶段：S?
是否动 holdout / promote / 真下单：是/否
后续 owner 命令：
```

已发生关键决策（摘要）：

| 日期 | 决策 | 影响 |
|---|---|---|
| 07-07 | YOLO+ LGBM 两层架构 | 项目骨架 |
| 07-18 | v11_reg freeze / ACTIVE | 现网判断指针 |
| 07-23 | 检测只认盘口；pre-v16 清除；detector=none | 实盘教义 |
| 07-23 | holdout#6 v16 证伪；#7 空边趋势证伪 | N=7 |
| 07-24 | short-only 全链路优先 | 主战场切换 |
| 07-24 | tip 重裁窗 + tip_v1b | 检测研究线 |
| 07-24 | short 主线 = 回归对齐 v11；关 binary | 判断哲学纠偏 |

---

## 9. 近端执行清单（给下一个会话）

**无需 Owner 即可做**

- [ ] 等/收 100×6m 扫池完成  
- [ ] `train --side short --objective regression` 出单切报告  
- [ ] （建议）5-fold walkforward JSON + 报告  
- [ ] 更新 HANDOFF 顶部 + todo 状态  
- [ ] 代码/报告提交（排除 runtime 垃圾）

**必须 Owner 点头**

- [ ] 开 1000 目视框包  
- [ ] 检测器 promote 申请  
- [ ] 换障碍 / 成本 / 阈值  
- [ ] holdout#8  
- [ ] 影子接 live / 真下单  
- [ ] Long YOLO 开训  

---

## 10. 文档地图（本计划相关）

| 文档 | 用途 |
|---|---|
| `HANDOFF.md` | 唯一实时真相 |
| `analysis/todo_short_only_pipeline.md` | short 待办勾选 |
| `analysis/p_short_only_pipeline.md` | short 作战细节 |
| `analysis/p_short_judgment_reg_align_v11.md` | 30 池回归结论 |
| `analysis/p_short_judgment_30_6m_reg_walkforward.md` | 稳健性降级证据 |
| `analysis/week_plan_20260720.md` | 旧周计划（历史） |
| `PROJECT_PLAN.md` | 07-07 三阶段（历史） |
| `docs/learnings/` | 非平凡结论库 |

---

## 11. 版本

| 版本 | 日期 | 说明 |
|---|---|---|
| v1 | 2026-07-24 | 按 short-only 纠偏后现状重写项目管理计划；替换周计划执行地位（周计划改历史阅读） |

**维护规则**：阶段跨越或 Owner 重大决策后，更新本文件版本表 + HANDOFF 顶部；勿静默改成功标准。

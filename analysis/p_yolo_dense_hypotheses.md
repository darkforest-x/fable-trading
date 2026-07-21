# YOLO 均线密集检测层假设簇（H-DET）— 发现级汇总

日期：2026-07-22  
授权：owner「参考 RESEARCH_AGENDA，针对 YOLO 检测均线密集提出假设并研究测试」  
纪律：发现级 · **未**耗 holdout · **未** promote · **未**打断 `owner_v13_pad200` 训练

议程正文：[`docs/RESEARCH_AGENDA_DETECT.md`](../docs/RESEARCH_AGENDA_DETECT.md)  
主议程指针：[`docs/RESEARCH_AGENDA.md`](../docs/RESEARCH_AGENDA.md)

## 结论先行

**调度/阈值不是解药；几何训练分布才是。** tip-only 与 TIP_CONF 已证伪抬 tip_fire。  
当前唯一值得等的单变量是 **H-DET-1（v13 pad200）**；训完用 true_tip + tip-smoke 贴边开火率对照 v12。

| 状态 | 假设 |
|---|---|
| 🔴 已证伪 | H-DET-5 tip 窗降 conf；H-DET-6 tip-only 调度；H-DET-8 当 tip 解药的 A′ |
| 🟢 已证实（发现级） | H-DET-3 右缘 N 验收口径；H-DET-7 离线 tip_hit≠实盘 tip_fire；H-DET-8 A′ 止血事后账 |
| 🔵/🟡 等 v13 | **H-DET-1 pad200**（训练中，epoch1+；终局评测脚本已备） |
| 🟡/⚪ 可排期 | H-DET-4 渲染消融；H-DET-2 中段硬负 |

**下一步唯一推荐**：等 `models/owner_v13_pad200.pt` 落盘 → `bash scripts/eval_v13_vs_v12_tip.sh`（H-DET-1）。

## 1. 假设表（人话）

| # | 人话 | 设计 | 判定 | 状态 |
|---|---|---|---|---|
| H-DET-1 | pad200（框后无后文）训出的 v13 比 v12 更能 tip 贴边开火 | `dense_owner_v13_pad200` finetune from v12 | tip-smoke 贴边开火 ≫ v12 的 0；true_tip 不崩 F1 | 🔵 训中；🟡 等终局对照 |
| H-DET-2 | 有后文的中段簇作硬负 → 抑制事后框 | 只加 hard-neg，其它不变 | 中段框↓ 且 tip 召回不塌 | ⚪ |
| H-DET-3 | 验收只评右缘 N 根有框，不只 mAP | tip_hit / tip_edge / tip_subset strict | 每份检测报告必报 | 🟢 |
| H-DET-4 | MA 线宽/颜色/留白影响 tip | 固定权重极小消融 | 开火率相对基线可测变化 | 🟡 协议已写 |
| H-DET-5 | tip 窗单独 conf 阈值抬 tip_fire | TIP_CONF=0.22 vs 0.30 | tip-smoke / tip_fire↑ | 🔴 |
| H-DET-6 | tip-only 调度抬 tip_fire | MODE=tip vs live | tip_fire↑ | 🔴 |
| H-DET-7 | true_tip tip_hit ≠ 盘口 tip_fire | v12 0.925 vs smoke 0/27 | 鸿沟存在 → 训分布问题 | 🟢 |
| H-DET-8 | A′ 贴边门止血事后账，不造 tip | TIP_EDGE_BARS=2 | 拒 KORU 类；tip_fire 仍可为 0 | 🟢止血 / 🔴解药 |

## 2. 已测结论（登记入库，本轮未重跑 GPU）

### H-DET-5 / H-DET-6 — tip-smoke（v12 主线）

来源：`analysis/p_tip_only_smoke.md` + `analysis/output/diag_tip_smoke.json`

| 条件 | 强制 tip 扫描（27 币） | 账本 tip_fire（32 行） |
|---|---|---|
| live@0.30 | **0/27** | 1/32 |
| tip@0.30 + TIP_CONF=0.22 | **0/27** | 1/32 |

→ 降 conf、换 tip-only **都不抬** 贴边开火率。根因是模型在无后文 tip 上几乎不画贴边框。

### H-DET-7 / H-DET-3 — 离线高 tip_hit vs 实盘

| 指标 | 数字 | 来源 |
|---|---|---|
| v12 true_tip tip_hit | **0.925** (111/120) | `p_v12_htip_eval` / `tip_rate_v12.json` |
| tip-smoke 开火 | **0/27** | diag_tip_smoke |
| tip_subset strict 折扣（净/全量净） | **0.0465** | `p_tip_subset_val` |
| tip_subset val tip_hit_strict | 3.4% (14/413) | 同上 |

→ 「金标重渲窗末=信号」协议上会开火，≠「盘口当下」会开火。验收必须带右缘/tip_edge 口径（H-DET-3）。

### H-DET-8 — box→bar + A′

来源：`analysis/p_box_to_bar_lag.md`（KORU tip−3、EDEN 中段）  
映射往返正确；语义错位。A′（最后 2 根入账）已上线：**挡事后账**，**不产生** tip。

### H-DET-1 — v13 现状（本轮）

| 项 | 值 |
|---|---|
| 进程 | `python -m src.detection.train ... --name owner_v13_pad200`（勿杀） |
| 数据 | `datasets/dense_owner_v13_pad200`（pad200 正样本 + 拷贝空标背景） |
| mid-run | `runs/.../owner_v13_pad200/weights/best.pt`（epoch1 量级） |
| 稳定权重 | **尚无** `models/owner_v13_pad200.pt` |
| 本轮动作 | **不**用 mid-run 抢 MPS 做对照；脚本 `scripts/eval_v13_vs_v12_tip.sh` 已写好 |

pipeline（`run_v13_pad200_pipeline.sh`）训完会自动 true_tip + frozen F1；本脚本补 tip-smoke 对照。

## 3. 复现 / 训完命令

```bash
# 等稳定权重出现后（或 pipeline 结束后）
bash scripts/eval_v13_vs_v12_tip.sh
```

渲染消融（H-DET-4，GPU 空闲；每次只改一项）：见 `docs/RESEARCH_AGENDA_DETECT.md` § H-DET-4。

## 4. 解读

1. **已关掉两条死路**：单独降 tip conf、永久 tip-only——别再当主药复读。  
2. **A′ 是账本卫生，不是检测能力**：新鲜分子仍取决于模型 tip 出生率。  
3. **pad200 是当前唯一在训的分布对齐实验**；若 tip-smoke 仍≈0，下一刀应是 H-DET-4（渲染）或 H-DET-2（硬负），不要立刻堆部署加速。  
4. tip_subset 折扣提醒：即便 tip 通了，实盘预期也不能拿全量 val 净收益叙事。

## 5. 风险与诚实声明

- 本报告**登记**既有诊断，**未**重跑 tip-smoke / tip_subset（避免抢 v13 MPS）。  
- mid-run `best.pt` 存在但 epoch 很早——用它宣称 H-DET-1 成败会误导；以训完稳定权重为准。  
- tip-smoke 本机常缺 VPS K 线；对照可能需 VPS 只读或账本+缓存对齐。  
- 发现级赢 ≠ 可 promote；确认级 = 前向新鲜 100。

## 6. 下一步（唯一推荐）

**H-DET-1 终局对照**：`eval_v13_vs_v12_tip.sh` → 写 `analysis/p_v13_pad200_train.md`。

| 若 tip-smoke 开火明显 >0 | owner 目视小样 → 再议影子/切流（需批准） |
|---|---|
| 若仍≈0 | 开 H-DET-4 极小渲染消融；并行策划 H-DET-2 硬负（需批准） |
| 不做 | 自动 promote、清 forward_log、放宽三门、再降 TIP_CONF |

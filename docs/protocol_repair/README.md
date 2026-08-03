# Short 协议修复计划(P0–P5)

**来源**:Notion《Fable Trading｜Grok Build 接管计划(2026-07-31)》,owner 2026-08-02 发出。
**Notion 是权威版本**,本目录是落盘副本,便于在仓库内(及给外部模型)阅读。
若两处不一致,以 Notion 为准并更新此处。

原文的必读清单写的是 `docs/grok_build/…`。**本仓库改用 `docs/protocol_repair/`** ——
计划的内容是 short 协议修复,那是项目自身的属性,与谁执行无关;
全仓库没有第二个以厂商或 agent 命名的目录,而 `MA206_RUNTIME_MIGRATION_CHECKLIST.md`
已经立了"按迁移什么命名"的先例。照 Notion 清单找不到 `grok_build/` 的人,看这一段。

## 本目录

| 文件 | 对应 Notion 页 | 内容 |
|---|---|---|
| `README.md` | 索引页 + 00 | 本文:定位、当前状态、落盘范围 |
| `P0_SAFETY_SPEC.md` | 02 | P0-01…P0-08 缺陷、协议对象设计、P0.0–P0.7 实施顺序 |
| `ACCEPTANCE_MATRIX.md` | 04 | A/B/C/D/E/F/G/H/I 九组验收断言 |
| `DECISIONS_AND_STOP_GATES.md` | 05 | D-01…D-10 已冻结决定、O-01…O-05 待 Owner、停止条件 |

**未单独落盘**:01(总体项目规划)、03(实施操作手册)、06(三层架构调研)。03 已在
2026-08-03 P0 执行时从 Notion 阅读并遵循；其核验命令、提交纪律和固定交付物已体现在
`analysis/p0_safety_protocol_repair_20260803.md`，不复制一份可能漂移的全文。

## 计划的核心判断(原文)

- 主线是 15m USDT-SWAP **short-only**,YOLO L1 候选 + LightGBM L2 排序。
- **v10 仅为 legacy / audit evidence,不是可执行 short bundle。**
- 最严重问题:short 意图在 forward 中进入 long feature、long barrier 和 `side="long"` 路径。
- 固定 q90 门并未形成 top-decile;历史 val 中因 score ties 约 **91.2%** 通过。
- 最终确认只认同一 `protocol_version` 下的新鲜前向样本,旧 forward_log 不得混算。

## 当前阶段与禁止事项(原文)

> 当前唯一允许启动的阶段:**P0-SAFETY**。在 P0 验收完成前,**不得重训、调阈值、
> 切换 ACTIVE、读取 holdout、部署 VPS 或启用真实 short 下单**。

## 与仓库实际状态的对账(2026-08-03)

计划书写在 07-31 的 lite 快照上,自己声明不得假设快照等于线上。**对账结果见
[`analysis/p0_baseline_audit_20260803.md`](../../analysis/p0_baseline_audit_20260803.md)**,
要点:

| 计划书条目 | 快照说 | 全量仓库实测 | 处置 |
|---|---|---|---|
| P0-01 short 写成 long | 存在 | 已修(`32e556b`) | — |
| P0-02 缺失 side 默认 long | 存在 | 曾仍在 | **已修** `61b4dc3` |
| P0-03 feature semantics 分叉 | 风险 | **已成实际故障** | **已修** `61b4dc3` |
| P0-04 barrier/return 分叉 | 存在 | **已修** canonical resolver + 显式 cost route | `ee98ebd` |
| P0-05 signal/decision/fill 混一体 | 存在 | **已修** causal fill timeline | `8e90390` |
| P0-06 artifact 非单一权威 | 存在 | **已修生产入口** exact bundle；research glob 保留 | `8cd2a56` |
| P0-07 tip age 缺全局断言 | 存在 | **已修** global age≤2 | `969dda7` |
| P0-08 signal_key 含 score | 存在 | 已修 | — |

**P0-03 曾是真实运行中的故障**:`32e556b` 按计划把 forward 改成 side-aware(正确),
但 `models/ACTIVE` 仍指着用 legacy 语义训练的 v10,于是短模型被喂进 6 个符号翻转的特征。
计划书 P0-03 明文警告过这半步。`61b4dc3` 让提取器跟 `feature_semantics` 走而非跟 side 走,
无需重训、未动 ACTIVE。

**计划书两个前提在本仓库不成立**:

- `data/forward_log.csv` 只有表头,0 行 —— 无旧记录可标 legacy;仅存 35 行在 `vps_rescue/`。
- VPS 已到期不续,取不到 live service / ledger 证据。

## P0 最终状态（2026-08-03）

P0.0→P0.7 已完成并停在 Owner gate。当前没有 active bundle，legacy v10 明确 audit-only；
完整验收与 parity 裁决见 `analysis/p0_safety_protocol_repair_20260803.md` 和
`analysis/p0_runtime_parity_audit_20260803.md`。

## 与本仓库既有纪律的关系

计划书的停止门与 `CLAUDE.md` / `AGENTS.md` 的铁律高度重合,**冲突时以铁律为准**
(铁律是 owner 直接下的,计划书是接管方案)。已知一致点:

- 计划书验收 I-07「全部提交在 main,无新 branch/worktree」= 铁律 13。
- 计划书 D-05「不碰 holdout」= 铁律 1(且本仓库要求记录第 N 次消耗)。
- 计划书 D-06「不清 forward log」= 实盘纪律 10。
- 计划书 D-09「全局 tip age ≤ 2」= 铁律 12(检测只认盘口)。

# 文档地图（2026-07-22）

**唯一实时状态**：仓库根目录 [`HANDOFF.md`](../HANDOFF.md) 顶部。  
**本周执行**：[`analysis/week_plan_20260720.md`](../analysis/week_plan_20260720.md)。  
**夜间旁路纪要**：[`analysis/p_overnight_20260722.md`](../analysis/p_overnight_20260722.md)。

## 活文档（会随阶段改）

| 文件 | 角色 |
|---|---|
| `HANDOFF.md` | 当前真相、holdout 账本、进行中 |
| `CLAUDE.md` / `AGENTS.md` | 铁律 + 实盘纪律（两文件保持同步） |
| `README.md` | 动机/架构/怎么跑（不堆日报） |
| `docs/ARCHITECTURE.md` | 现行系统图与模块地图 |
| `docs/RESEARCH_AGENDA.md` | 假设状态表 + 优先队列（含 H-FE / H-TOOL） |
| `docs/RESEARCH_AGENDA_DETECT.md` | 检测层 H-DET 子簇（tip/pad200/渲染）；汇总见 `analysis/p_yolo_dense_hypotheses.md` |
| `docs/DENSE_CLUSTER_DEFINITION.md` | 形态视觉定义（标杆） |
| `docs/LOCAL_DEBUG_TOOLS.md` | 本机 nvitop/netron/LWC·叠框命令（不抢 MPS） |
| `docs/EXEC_PROTECTIONS_SPEC.md` | Freqtrade Protections→executor 规格（不引 GPL） |
| `docs/ops/VPS_OBSERVABILITY_PENDING.md` | Kuma/Grafana 等 **待 Owner 批** |
| `analysis/week_plan_*.md` | 当周执行计划 |
| `analysis/p*_report.md` | 单次实验记录（只增不改结论） |
| `analysis/p_wuzao_topics_scan.md` | wuzao 全站可迁移清单（A/B/C/D） |
| `analysis/backlog_future_optimizations.md` | tip 通后再拧的积木 |
| `docs/learnings/*` | 事故/反直觉（只增） |

## 历史 / 已合入 / 只读

| 文件 | 说明 |
|---|---|
| `PROJECT_PLAN.md` | 07-07 三阶段路线图；顶注已标「阶段完成→实盘」 |
| `docs/archive/*` | NEXT_STEPS / PROJECT_STATUS 等已并入 HANDOFF |
| `docs/FORWARD_ACCELERATION_OPTIONS.md` | 07-10 加速 N 备忘；默认 stay |
| `docs/H1_SCALED_FORWARD_SHADOW_PLAN.md` | H1 shadow 设计；已实现、非主线 |
| `docs/OWNER_LABELING_PLAYBOOK.md` | 打标流程；当前阻塞是 H-TIP 非堆轮次 |
| `docs/P2_5_*` | Ops 台 Phase0–3 说明；已合主线 |
| `docs/LABEL_REVIEW_TOOLS.md` | FO/LS 审查工具 |
| `output/offline_tasks/*` | 多日无人值守快照；数字会旧 |
| `analysis/p*.md`（非当周） | 实验报告；**勿改历史结论** |

## 不要做的文档维护

- 不要平行维护第二份「当前状态」  
- 不要改旧 `p*_report` 的结论数字去「对齐现状」  
- 改纪律时 **CLAUDE.md 与 AGENTS.md 必须同改**  

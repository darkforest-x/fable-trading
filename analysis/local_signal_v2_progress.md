# Local Signal V2 — 进度一页纸

**更新**：2026-08-07 07:25 UTC · **授权**：owner 全文生效

## 禁止（即使全权）

- promote ACTIVE / owner_best · 真下单 · 清 forward_log · 未记账额外 holdout

## 当前状态

| 阶段 | 状态 | 产物 |
|---|---|---|
| P0 Stage B | ✅ 七道门全绿 | `datasets/local_signal_v2_stageb` |
| P0 报告 | ✅ | `analysis/html/p0_local_signal_v2_stageb_report.html` |
| P1 冷启动 60ep | ✅ **完成** 1.315h | best mAP50=0.771 / mAP50-95=0.572 |
| P1 报告 | ✅ | `analysis/html/p1_local_signal_v2_stageb_cold_report.html` |
| 权重 | ✅ 已拉回 | `analysis/output/lsv2_stageb/owner_lsv2_stageb_cold/weights/best.pt` |
| tip/event 验收 | 🔄 下一步 | 交易级门未过 → **不自动 P2** |
| P3 paper 脚手架 | ✅ | `scripts/forward_paper_local_signal_v2_scaffold.py` |
| w20 旁路 | 🔄 | preholdout / shadow / gallery |

## 决策

`reports/ACCEPTANCE_DECISION.json` → phase=P1 · **needs_more_data**（等 tip 对照）

## 命令

```bash
open analysis/html/p1_local_signal_v2_stageb_cold_report.html
cat reports/ACCEPTANCE_DECISION.json
ls -la analysis/output/lsv2_stageb/owner_lsv2_stageb_cold/weights/best.pt
```

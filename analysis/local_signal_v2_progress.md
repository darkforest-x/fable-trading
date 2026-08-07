# Local Signal V2 — 进度一页纸

**更新**：2026-08-07 · **授权**：owner 全文生效

## 禁止（即使全权）

- promote ACTIVE / owner_best · 真下单 · 清 forward_log · 未记账额外 holdout

## 当前状态

| 阶段 | 状态 | 产物 |
|---|---|---|
| 授权落盘 | ✅ | 本文件 + `reports/ACCEPTANCE_DECISION.json` |
| P0 Stage A 审计 | ✅ FAIL（预期） | `analysis/p0_local_signal_v2_audit_20260807.md` |
| P0 Stage B 重建 | ✅ | `datasets/local_signal_v2_stageb` 2388pos+2388neg |
| P0 自检门 | ✅ **全绿** | `analysis/output/p0_local_signal_v2_stageb_audit.json` |
| P0 报告 | ✅ | `analysis/html/p0_local_signal_v2_stageb_report.html` |
| P1 冷启动 3060 | 🔄 | `owner_lsv2_stageb_cold` |
| P2 hardneg | ⏳ | 待 P1 C 优 |
| P3 paper 脚手架 | ✅ 骨架 | `scripts/forward_paper_local_signal_v2_scaffold.py` |
| w20 旁路 | 🔄 | tip preholdout ~129/311；shadow；hn030 画廊 |

## 关键检查点

- `p0_pass=True` · decision=`accepted` · commit `bed5e64` 已 push main  
- ACTIVE / owner_best / forward_log.csv：**未动**

## 命令

```bash
bash scripts/train_local_signal_v2_stageb_on_3060.sh --status
cat reports/ACCEPTANCE_DECISION.json
open analysis/html/p0_local_signal_v2_stageb_report.html
```

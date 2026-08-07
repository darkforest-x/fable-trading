# Local Signal V2 — 进度一页纸

**更新**：2026-08-07 · **授权**：owner 全文生效（P0→P1 自检绿自动进；C 优则尽量 P2；P3 仅 paper/forward 脚手架）

## 禁止（即使全权）

- promote ACTIVE / owner_best  
- 真下单 / 清 forward_log  
- 未记账的额外 holdout（V2 最终验收预留 **1 次**，报告写第 N 次）

## 当前状态

| 阶段 | 状态 | 产物 |
|---|---|---|
| 授权落盘 | ✅ | 本文件 + `reports/ACCEPTANCE_DECISION.json` |
| P0 旧审计（Stage A w20） | ✅ 已测、**未通过** | `analysis/p0_local_signal_v2_audit_20260807.md` |
| P0 Stage B 重建 | 🔄 进行中 | `datasets/local_signal_v2_stageb` |
| P0 自检门 | ⏳ | 七道硬门槛全绿才进 P1 |
| P1 小样本对照 | ⏳ | 待 P0 |
| P2 hardneg | ⏳ | 待 P1 C 优 |
| P3 paper/forward 脚手架 | ⏳ | 不 promote |
| w20 旁路 | 🔄 | tip preholdout 回测中；shadow bootstrap；hn030 画廊 |

## 裁决记录

- **Stage A w20 不得进 P1 全训**：95% 样本含 decision 后未来 K；symbol 哈希切；246 张 holdout 进训练。
- **全权下的补救**：新建 Stage B 因果数据集（不覆盖 Stage A），过门后再 P1。
- **ACTIVE / owner_best / forward_log.csv**：未动。

## 检查点命令

```bash
.venv/bin/python scripts/audit_local_signal_v2.py --dataset datasets/local_signal_v2_stageb
cat reports/ACCEPTANCE_DECISION.json
```

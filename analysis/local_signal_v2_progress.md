# Local Signal V2 — 进度一页纸

**更新**：2026-08-10 13:34 UTC

## 当前裁决

P0 数据修复已通过；P1 被重置，等待 owner 决策。旧 Stage-B 数据的负样本没有真正按时间切分，旧权重也使用过非零 HSV，因此不能作为修复后 V2 候选。

| 阶段 | 状态 | 产物 |
|---|---|---|
| V1 Stage B | ❌ P0 FAIL | train 越界 negatives 317；val 过早 negatives 296 |
| strict-negative V2 | ✅ P0 8/8 PASS | `datasets/local_signal_v2_stageb_strictneg_v2` |
| Builder Git 锚点 | ✅ | `471f854`；数据在提交后重建 |
| P0 报告 | ✅ | `analysis/html/p0_local_signal_v2_stageb_strictneg_v2_report.html` |
| 24-event preview | ✅ 24 个不同币种 | `analysis/output/local_signal_v2_stageb_strictneg_v2_preview/` |
| 旧 P1 cold 权重 | ❌ invalidated | 绑定 V1 数据 + hsv_s/v=0.05；禁止冒充 V2 |
| 新 P1 | ⏸ 未训练 | 需 owner 批准并先冻结 A/B/C 对照与 event gates |
| P2 / P3 | ⛔ 不进入 | P1 未完成 |

## 禁止

- 自动进入 P1 / P2
- 复用旧权重作为 strict-negative V2 结果
- promote ACTIVE / owner_best
- 真下单、清 forward_log、未批准读取 holdout

## 机器裁决

`reports/ACCEPTANCE_DECISION.json` → phase=P0、decision=accepted、`p1_train_complete=false`。

```bash
open analysis/html/p0_local_signal_v2_stageb_strictneg_v2_report.html
cat reports/ACCEPTANCE_DECISION.json
.venv/bin/python scripts/audit_local_signal_v2.py \
  --dataset datasets/local_signal_v2_stageb_strictneg_v2
```

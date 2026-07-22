# v13 val mAP 崩 ≠ tip 裁决；tip-smoke 才是

- **问题**：Owner 看到 v13 pad200 官方 val P/R/mAP≈0.11/0.05/0.027，容易当成「模型废了」。
- **死胡同**：用 Ultralytics val 表对 v12（mAP50≈0.53）做「差 20×」判决——v13 val 标签刻意保持 v11 中段金标（frozen-F1 尺子），train 却是右缘贴窗末的 pad200，分布错位会系统性压垮 val mAP。
- **有效路径**：终局后跑 `scripts/eval_v13_vs_v12_tip.sh`；发现级主指标是 tip-smoke 贴边开火 + true_tip tip_hit。2026-07-22：tip-smoke 仍 0/27，true_tip 0.008——**诚实记 H-DET-1 未过**，但叙事必须分开「val 预期崩」与「tip 也未过」。
- **通用规则**：检测实验报告先写 tip-smoke / tip_hit，再附 val mAP 并标注 val 是否同几何；凡 train/val 几何故意不一致，val mAP 只能作辅表。
- **牵连**：`dense_owner_v13_pad200` val=未 pad；`docs/RESEARCH_AGENDA_DETECT.md` H-DET-1/3/EXT-3；`analysis/p_v13_pad200_train.md`。

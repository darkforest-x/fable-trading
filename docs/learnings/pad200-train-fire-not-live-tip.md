# pad200 训图能开火≠盘口 tip

- **问题**：v14 MAD-on 修了错窗后 tip_hit 仍 0.033、tip-smoke 仍 0/27；需要区分「标签坏 / 渲坏 / 学偏 / 语义错」。
- **死胡同**：把失败归因成「再修标签再训一轮」；或看到 val mAP 从 0.027→0.155 就以为 tip 在进步；把 true_tip tip_hit 与 tip-smoke 混谈。
- **有效路径**：同图对照——存档 pad200 vs `process_pad200` MAD=0；v14 在训图上贴右开火，在 true_tip（slice-MA）与 tip-smoke（当前 tip）上静默。结论：学到了 pad200 分布，未迁移到盘口 tip；中段 val early-stop 放大遗忘。
- **通用规则**：检测实验失败时先问「模型在训练正样本上还开火吗」；若训图开火、评测归零，优先查分布/协议语义，而不是默认标签崩坏。true_tip 与 tip-smoke 必须分表记账。
- **牵连**：`analysis/p_v14_failure_rootcause.md`；H-DET-1/7；`scripts/tip_detectability.py`（slice-MA）vs `yolo_candidates.scan_series_with_yolo`（full-MA）。

# MAD-on 复验仍过不了 tip-smoke

- **问题**：v13 pad200 tip 崩（tip_hit 0.008 / tip-smoke 0/27）后，怀疑主因是关 MAD 导致 okx 错窗；v14 用 MAD 默认开重建同协议数据再训，看能否救 tip。
- **死胡同**：把「修标签」等同于「过 H-DET-1」；或拿 v14 val mAP（0.155≫v13 0.027）当 tip 进步。val 仍是中段金标，mAP 抬只能说明学得没那么惨，不能代表贴边开火。
- **有效路径**：同口径 true_tip + tip-smoke 对照 v12/v13。v14 tip_hit 0.033、smoke 仍 0/27 → 发现级未过；结论是 pad200「无后文」协议（或训推渲染差）未解，不是「再修一轮标签再训」。
- **通用规则**：标签审计通过后若 tip-smoke 仍≈0，停止同构数据再训；下一步换单变量（渲染消融 H-DET-4 / 硬负 H-DET-2），不要用 mAP 反驳 tip 失败。
- **牵连**：`models/owner_v14_pad200.pt`；`analysis/p_v14_pad200_train.md`；`docs/RESEARCH_AGENDA_DETECT.md` H-DET-1；`scripts/eval_v14_vs_v12_tip.sh`。

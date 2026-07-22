# tip 崩盘先查几何审计再怪标签坏

- **问题**：v13 pad200 tip_hit 从 0.925→0.008，Owner 直觉「训练集是不是坏了」，需要快速区分标签/渲染崩 vs 协议错位 vs 真学崩。
- **死胡同**：只盯官方 val mAP（train tip vs val 中段必烂）当失败证明；或反过来用「val 预期烂」掩盖 tip-smoke/true_tip 同烂；未分层抽查就重训。
- **有效路径**：先报正/背景数、右缘与框宽分位、对照上一版；再叠 GT 抽 20 张（典型贴右 + 极端 + 背景）；最后把 tip_hit 明细里「是否 0 框」写进归因。本轮结论：标签几何按设计贴右、渲染正常 → tip≈0 是学崩/协议差，不是坏标签。
- **通用规则**：检测训差先做「几何审计 + 目视叠框 + tip 明细是否静默」，再开下一假设；train/val 故意不同几何时 mAP 只能作辅表。
- **牵连**：`datasets/dense_owner_v13_pad200`；`analysis/p_v13_why_bad_train.md`；`analysis/output/v13_train_sample20/`；H-DET-1/3/4；`docs/learnings/v13-val-map-is-not-tip-verdict.md`。

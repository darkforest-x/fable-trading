# 未匹配状态也可能包含已经抽到但结果删失的控制

- **问题**：趋势基线统计把控制删失计为 `matched & control_censored`，得到零；底层抽样器在回放删失时会同时把 matched 置为 false，原账本实际有 200 条 reason=censored。
- **死胡同**：把 matched 理解成“抽到了候选”，再与删失相交。这两个状态在既有接口里互斥，增加统计样本不会暴露逻辑错误。
- **有效路径**：先读控制状态契约，按 reason=censored 统计删失；添加 matched=false、reason=censored 的合成反例。保留 statistics_v1 和原报告，在 statistics_v2 修计数，逐表确认收益、配对与检验结果不变。
- **通用规则**：复用布尔状态之前检查它代表候选存在、路径存在还是最终可用；不要把同名字段当成通用语义。
- **牵连**：`spike_v9_htf_sma_study.controls`、`trend_baseline_report.control_metrics`。控制删失计数包含在 unmatched 中，不可把二者相加；不改变抽样、删失排除或收益。

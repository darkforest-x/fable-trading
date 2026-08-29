# 先达标签不是交易胜负标签

- **问题**：Pine V9 的 335 个原始候选中，`+1.5%` 先于初始止损/反向信号的正例率达到 54.93%，但实际动态账本净胜率仍只有约 14%–19%；把 ATR 扩张候选的先达率提高后，2023 资金收益反而从 +70.50% 降到 -2.24%。
- **死胡同**：把 `+1.5%` 当成“盈利交易”。在冻结策略里它仅在 15m bar 完成后为下一根 bar 启用 `+0.1%` stop；扣 20bp 往返成本后，这类 BE 退出仍是 -10bp。入场 gate 还会改变反转、cooldown、仓位与尾部盈利路径，所以标签提升不能代替动态经济回放。
- **有效路径**：把 first-touch 只作为“启动质量”的辅助标签；任何基于它的规则先在完整 335-row raw surface 上产生决策，再进入原状态机动态回放，并同时报告成本后收益、回撤、去 top1、匹配随机对照和区块检验。
- **通用规则**：设计判断层标签时，第一步写出“标签事件”和“真实结算事件”的差异表。只要标签触发后仍有路径依赖的退出管理，它就不能被称为 win label，也不能用分类胜率推算策略收益。
- **牵连**：`yoyo/layers/l2_judgment/pine_start_labels.py`、`scripts/audit_pine_eth_15m_start_labels.py`、`scripts/evaluate_pine_eth_15m_atr_expansion_gate.py`、冻结 `+1.5%/+0.1%` BE 与 20bp 成本；延伸自 [状态型入场 gate 必须在回放状态机内评分](stateful-entry-gates-must-be-scored-inside-the-replay.md)。

# 研究结果不能归属于另一份运行时制品

- **问题**：研究优胜 arm 与当前 ACTIVE 可能共享币池、方向和目标名称，却在样本行集、特征 schema、训练轮数、selector 或模型身份上不同；只看共同标签会把研究收益错误归给生产模型。
- **死胡同**：用“都是 short regression”或“都来自 wide pool”证明等价，也不能用某个历史指标补齐不存在的冻结 model SHA。共同目标不是运行时同构证据。
- **有效路径**：逐项核对 candidate row identity、feature names/semantics、objective/target、rounds/early stopping、selector operator/tie/pass/equal、return/cost route 和 model SHA；任一关键项不同就把 parity 判为 rejected，并禁止收益结论转移或自动 promote。
- **牵连**：`models/ACTIVE`、exact bundle、研究诊断 JSON、runtime parity 报告和所有“当前模型继承历史 lift”的表述。没有单一冻结研究模型时，诚实结论只能是 research-only。
- **最小防回归**：机器可读 parity matrix 必须同时记录两侧的行数、特征数、训练轮数、selector 统计与模型身份；审计测试确认 28-feature/1-tree ACTIVE 不会被标为 47-feature/250-round research arm。

共同目标与共同数据源只能说明两条实验有联系，不能证明它们是同一配置。生产结果的最小身份是“数据行集 + 特征语义 + 训练规则 + selector + model hash”；身份未闭合时，收益归属必须拒绝。

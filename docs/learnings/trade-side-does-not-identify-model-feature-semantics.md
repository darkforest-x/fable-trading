# 交易方向不能推断模型特征语义

- **问题**：short 是交易意图，但历史 v10 模型用未对齐的 28 维向量训练；若推理端看到 `side=short` 就自动翻转方向特征，会把新坐标系喂给旧模型。
- **死胡同**：让 extractor 跟随 trade side。这个规则在“所有 short 模型都按 short-aligned 重训”之后才可能成立，对 legacy artifact 会制造 train/serve 反号而不是修复。
- **有效路径**：把 `feature_semantics` 作为 bundle 的必填 enum；生产入口传入 protocol，extractor 只按 `legacy_unaligned` 或 `side_aligned_v1` 选择。legacy + execution eligible 在加载期非法；28 列 deterministic snapshot 与 as-of 测试固定两个坐标系及因果边界。
- **通用规则**：模型输入坐标系属于训练 artifact，不属于业务动作；serve 必须复现训练时声明的变换，不能从目标方向、模型名字或当前策略猜测。
- **牵连**：`src/judgment/features.py`、`src/judgment/forward_scan.py`、`src/judgment/protocol.py`、`tests/test_forward_feature_semantics.py`；P0 D-01～D-06。v10 继续 `legacy_unaligned` 且 audit-only，P0 不重训。

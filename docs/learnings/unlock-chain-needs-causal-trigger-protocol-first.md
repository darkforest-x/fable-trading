# 打通标签到交易必须先冻结因果触发协议

- **问题**：owner 框→特征→全市场规则反复 train 薄正 / holdout 塌；需要把「打标→因子→可交易」重设计成可执行阶段，而不是再挖因子。
- **死胡同**：①把框右缘当 tip 提特征全市场扫；②分边/116 因子/趋势出/E1 宽对齐/E2 atr/E3 稀疏当续命；③用 oracle/AUC/召回↑当打通；④同一 A 再烧 holdout。根因是确认态标签 + 错误裁判 + regime 不迁移，不是缺列。
- **有效路径**：Phase 0 先选 tip vs 确认态并冻结「当下可判」触发 bar → Phase 1 小样重标/回拨测时点（Jaccard/精度优先）→ Phase 2 只在事件窗学规则且必须有部署等价扫描 → Phase 3 train 因果分边双报出场 → Phase 4 预注册才 holdout → Phase 5 影子实盘。LGBM 只披露；YOLO 仅 T0a。
- **通用规则**：链路不通时先问「触发 bar 在当下能否判定」，再问特征；禁止跳过协议直接全市场扫。Holdout 只裁决冻结配置，不裁决「形态哲学」。
- **牵连**：`analysis/p_how_to_unlock_label_to_trade_chain.md`；上游 `p_chain_failure_attribution` / `p_entry_align_and_regime` / `p_e3_*` / `p_owner_side_*` / holdout#7；learnings `owner-label-oracle-alpha-is-not-causal-tip-alpha` / `chain-failure-is-regime-plus-entry-mismatch`。

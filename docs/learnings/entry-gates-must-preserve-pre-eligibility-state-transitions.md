# 入场 Gate 必须保留资格门之前的状态转换

- **问题**：给 Pine V9 接外部 LR/L2 分数时，完整特征面只包含通过日历与波动门的候选；但 allow-all 动态 replay 仍无法复现原 V9 账本。原因是冷静期计数在原始信号出现时先递减，之后才检查该信号是否允许入场。
- **死胡同**：把 score pass 直接 AND 到 `v9_long` / `v9_short`，并把特征面之外的信号全部置假。这样虽然看似 fail-closed，却删掉了不可入场原始信号对 cooldown 的副作用，后续候选的可执行状态随之漂移。
- **有效路径**：先按状态机真实顺序区分两类信号。不可入场原始信号不需要模型分数，但必须只为 cooldown 转换原样透传；可入场候选才要求全覆盖、因果及时且哈希锁定的分数，再由 gate 决定是否送入状态机。最后用 allow-all 分数证明每个时间切分的 entry、exit、方向、原因与数值逐笔恒等。
- **通用规则**：在任何 stateful 策略前加 gate 时，第一步画出“信号 → 状态副作用 → 资格门 → 入场”的实际执行顺序；身份 gate 必须先通过逐笔恒等测试，再测试拒绝路径。不能仅凭候选表的行定义推断被排除事件没有状态影响。
- **牵连**：`scripts/replay_pine_eth_15m_judgment_gate.py`、`yoyo/layers/l3_backtest/pine_allin_v7.py`、Pine 的 `tradesToSkip`、日历/波动门、335 行 raw-guarded candidate surface，以及任何未来 LR/LightGBM 动态回放。

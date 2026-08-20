# 验证门之间不能循环背书

- **问题**：轻量 artifact smoke 把“canonical validator 已通过”当成自身条件，而 canonical validator 又要求 Docker smoke 通过；一旦任一旧产物变成 fail，就无法通过正常重跑恢复。
- **死胡同**：调整运行顺序或保留一份旧的 pass JSON 只能掩盖循环，并让最终状态依赖历史缓存，而不是当前产物。
- **有效路径**：让底层 smoke 只独立重算账本、成本、统计失败和边界事实，不读取 validator 结论；上层 validator 可以消费 smoke 结果并汇总。依赖图因此恢复为单向。
- **通用规则**：每个验证层只证明自己能从更底层事实推出的命题；若 A 的通过需要 B、B 的通过又需要 A，先拆循环，再谈运行顺序。
- **牵连**：`scripts/smoke_pine_eth_15m_artifacts.py`、`scripts/validate_pine_eth_15m.py`、Docker 离线复验和对应测试；不涉及策略参数、市场数据或收益计算。

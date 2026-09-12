# 信号账本的 admission 必须来自执行掩码

- **问题**：共享执行器建立 raw signal ledger 时默认把事件标为 admitted；真实模拟器却另收 admission mask，导致成交正确但信号计数和 V7 保留率全部显示为通过。
- **死胡同**：把 simulator 内部的默认 ledger 字段当作策略选择事实，或只用成交数间接推断 admission，都无法区分被过滤、被占仓跳过和未触发。
- **有效路径**：回放层按 signal 时间把实际 admission mask 回填到 ledger，并为每个 trade table 显式写入 variant；用真实八臂执行器、拒绝 V7 mask 和 closed trade 的端到端测试守住该契约。
- **通用规则**：当执行器接收“原始事件 + 允许掩码”两个输入时，所有诊断账本都必须记录后者，不能复用原始事件构造时的默认值。
- **牵连**：`yoyo/evaluation/spike_v7_v1_compare.py`、`tests/evaluation/test_spike_v7_v1_compare.py`、`simulate_v6_variant`、V1/V6/V7 admission 和 tail-retention 统计。

# 共享反向退出源需要明确同 bar 优先级

- **问题**：V1 多头入场与原始 V6 空头退出源可在同一确认 bar 同时出现；把两侧直接交给双向信号接口会成为歧义事件，或让退出和新多头同时发生。
- **死胡同**：依赖执行器偶然的循环顺序，或悄悄删除其中一侧，都会让 common-execution 口径不可审计，也会掩盖冲突频率。
- **有效路径**：保留原始 V1 long 与 raw V6 short 的诊断列；同 bar 时退出源优先，抑制 common-execution V1 long，并按时间戳写出 conflicts。原生 V1 账本不经此适配器。
- **通用规则**：不同策略共享反向退出源时，先定义同 bar 的优先级并将被抑制事件作为产物输出，不能把语义决定藏在交易循环里。
- **牵连**：`yoyo/evaluation/spike_v7_v1_compare.py`、`tests/evaluation/test_spike_v7_v1_compare.py`、逐流 `conflicts.csv`、V1 common-execution/V6 exit-feed 合约。

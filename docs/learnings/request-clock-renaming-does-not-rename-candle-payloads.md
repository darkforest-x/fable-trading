# 请求时间改成 K2，不代表继承的烛身字段也变成 K2

- **问题**：V35 准备把真实主动成交量接到旧 K1/K2 名单；旧请求的 signal_time 是 K2，但 signal_open/high/low/close 仍来自 K1。
- **死胡同**：直接按通用 signal_* 字段聚合，就会把两根不同烛线的时间和形态拼接；文件名为 development 也不能证明内容已经按开发期截断。
- **有效路径**：先追踪请求构造和写出路径，再只投影 mother_signal_time / mother_decision_time / k2_time / decision_time，逐行校验间隔和母事件身份。V4 等待终态另存，不合入 K1 特征。
- **通用规则**：复用旧事件名单先核对每个字段的来源和可见时刻；身份、时钟、载荷可能在不同步骤改写，不能凭同名前缀推断语义。
- **牵连**：yoyo/data/hourly_impulse_k2.py；yoyo/evaluation/k1k2_genuine_flow_audit.py；V4 原母群与 V35 只读投影。不得把该子群包装成全市场候选集。

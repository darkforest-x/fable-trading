# 观察缓存必须包含源码、配置、事件版本和数据截止时间

- **问题**：同一配置但事件目录更新后，看板可能继续显示旧审核结论；同cutoff修订还会污染状态事件。
- **死胡同**：仅按config_hash读取最新快照或仅按scan_id去重，不能约束同一决策时间的版本。
- **有效路径**：完整命名空间包含schema、mode、config、source、catalog；同命名空间相同cutoff不同内容拒绝，版本变化产生新序列。
- **通用规则**：缓存键必须覆盖决定输出的全部输入版本；生成时间不能替代观察截止时间。
- **牵连**：yoyo/rotation/journal.py、server.py、pipeline.py。

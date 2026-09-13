# CSV 身份数字在分组前必须按领域类型归一化

- **问题**：候选上下文中的周期和连续段原本是整数，但经过混合 DataFrame 与 CSV 往返后成为 `"30.0"`、`"0.0"`；随机对照直接调用 `int()`，导致全部目标 fail-closed、0 个匹配。
- **死胡同**：把 0 匹配当作市场上找不到对照会掩盖身份解析错误；仅在异常处使用 `int(float(value))` 又会静默接受 `30.5` 这类非法身份。
- **有效路径**：在读取候选表的单一入口用 `pd.to_numeric` 解析，显式检查有限值且余数为零，再转为整数 dtype；后续分组、manifest 查找和 cache 构建只接收规范身份。
- **通用规则**：CSV 没有稳定数值类型，周期、segment、side 等身份字段必须在数据边界一次性验证和归一化；异常后的全量 fail-closed 还要按 reason 聚合，0 匹配不得直接作为研究结果。
- **牵连**：`yoyo/evaluation/spike_market_breadth_matched_controls.py` 的 `_read_targets`，以及 decimal identity 的聚焦回归测试。

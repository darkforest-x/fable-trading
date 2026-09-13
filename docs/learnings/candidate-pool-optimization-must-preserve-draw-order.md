# 候选池加速必须保留抽样顺序

- **问题**：匹配随机对照为每个 target 重建全时钟布尔掩码，运行时间随 target 数和流长度相乘。
- **死胡同**：用集合或无序容器装候选会改变 `flatnonzero` 的升序，再用相同哈希取模也会抽到不同 control，悄悄改变研究结果。
- **有效路径**：按 side、日历月、波动桶预建升序位置池，预先应用固定 eligibility、反向信号与所有 target bar 排除；抽样仍使用原 SHA-256 取模，并从每个相关池按位置删除已用 control。
- **通用规则**：随机选择的加速只有在候选集合、排序、种子输入、取模和无放回时点都逐项相等时才是语义等价；测试必须保留旧逻辑参考实现逐列对照。
- **牵连**：`yoyo/evaluation/spike_market_breadth_matched_controls.py`、其 manifest 的 outputs 钉点，以及 `tests/evaluation/test_spike_market_breadth_matched_controls.py`。

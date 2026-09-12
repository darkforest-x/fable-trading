# 空数据流合并后，布尔取反可能变成整数

- **问题**：完整 V7 研究汇总到未保留交易时，索引掩码出现 -1/-2，报告拒绝生成。
- **死胡同**：小型测试只有非空 DataFrame，exact_retained 是 bool，取反正常。全市场含很多无交易 CSV，合并使实际 True/False 的列成为 object；对 Python bool 对象做位反转得到整数，既可能报错，也可能污染其他按位组合统计。
- **有效路径**：在回执校验和 CSV 合并后，先验证值确实是 bool，显式统一 dtype，再执行任何掩码统计。控制组匹配缺失单独定义为未匹配；其他必要标志缺失则拒绝。测试覆盖 object 布尔和字符串 False 必须拒绝。交易回放无需重跑，报告汇总器修正后重新核算所有统计。
- **通用规则**：多流表格拼接后重新验证 schema，不能把单文件 dtype 当成聚合后的契约；尤其在使用位运算符之前。
- **牵连**：yoyo/evaluation/spike_v7_episode_report.py、tests/evaluation/test_spike_v7_episode_report.py；不改入场、退出或任何行情数据。

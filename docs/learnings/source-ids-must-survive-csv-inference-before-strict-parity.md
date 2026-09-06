# 严格回放比较之前，来源编号必须先保住文本语义

- **问题**：V17第一次原始回放在OFF基准的case_trades比较处停止；CSV的两个来源分段编号被推成整数，而执行器输出的是字符串。错误显示`0 != 0`，不是交易结果不同。
- **死胡同**：放宽全局parity或读完再astype(str)，既会吞掉真实错号，也救不回`007`变成`7`、字面量`nan`变成缺失的原信息。
- **有效路径**：先用不读行情的完整V16合成交易CSV往返逐列定位，只在V17父表reader对`partial_fast_initial_management_segment_id`和`partial_fast_initial_raw_segment_id`使用读入时converters；通用reader默认行为、其他字段和严格parity均不变。12项回归覆盖完整表、0、007、空串、nan、0.0、错号和默认API，相关121项通过。
- **通用规则**：来源ID是身份而不是测量值。序列化契约必须先于类型推断恢复；不能用更宽容的比较替代无损读取。保留第一次失败目录和源码哈希，第二次回放如实计数。
- **牵连**：V17研究runner、native_exit_research的可选saved_reader。策略、20bp门、样本、价格时间范围、成本与parity标准没有变化；此修复不证明策略盈利。

来源：[pandas2.3.3 read_csv converters](https://pandas.pydata.org/pandas-docs/version/2.3.3/reference/api/pandas.read_csv.html)。

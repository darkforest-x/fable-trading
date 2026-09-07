# 来源校验必须按生产者的实际回执格式，不按相邻实验猜

- **问题**：V32沿用V30的来源检查，误以为V31 summary含sources；实际只有started/support_frozen保存来源清单，summary保存其SHA和builder_commit。
- **死胡同**：照搬相邻实验validator并在合成fixture里补出不存在字段，只能验证自己的错误假设，真实运行会在标签读取前KeyError。不能为兼容新consumer改写历史summary。
- **有效路径**：先检查冻结V31元数据结构；比较started与support_frozen来源清单，比较三份builder_commit，再逐条对独立audit哈希与历史git blob。用刻意不含summary.sources的合成回执做回归测试，并在真实标签读取前运行只读provenance smoke。
- **通用规则**：复用实验runner先记录每一份生产者回执的实际字段和职责；跨格式验证以SHA/commit连接，不制造历史字段。测试fixture必须反映真实缺省字段。
- **牵连**：V32 volume_wave_economic_research及其tests；V31历史文件保持逐字节不变。未改变门/成本/结果，也没有因此新增holdout读取。

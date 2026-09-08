# 汇总格式失败时从冻结分片修复输出，不重跑模型

- **问题**：扩大样本的Pandas多列分组把周期／方向键生成为numpy.int64，普通json.dumps无法序列化。最小合成复现已确认，错误发生在全部模型结果保存后的汇总输出阶段。
- **死胡同**：直接修改正在运行的冻结runner或重跑全部检测。前者会让source_manifest及已完成分片身份失配，后者耗时且增加不必要的模型／数据接触，仍不能修复整数格式错误。
- **有效路径**：保留原源文件、manifest和216份带哈希的分片收据；另提交只读分片的finalizer。它核对完整分片与四份总账，复用完全相同的汇总和置换规则，仅显式把NumPy标量转换为Python原生数值，拒绝陌生对象及非有限值，独占创建最终summary。它不加载模型，不再读原始行情。最后由独立verifier复核全部结果。
- **通用规则**：先确认失败发生在推理、决策还是输出。只要市场结果及来源已经完整冻结，格式修复应从保存证据继续；不可把输出错误当作重新训练／调参／重跑模型的理由。
- **牵连**：`yoyo/evaluation/imacd_yolo_expanded_finalize.py`、`tests/test_imacd_yolo_expanded_finalize.py`及expanded实验的source_manifest、分片receipt和最终summary中的finalizer来源字段。原112项测试不含多列分组JSON序列化；本次补最小回归测试，不改已冻结测试文件。

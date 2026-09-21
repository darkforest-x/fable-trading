# 比较报告要验证上游预测链，不能只验证交易文件

- **问题**：V5新旧目标比较器核对了串行交易及3,531流叶文件，却未解引用串行receipt记录的预测/评价SHA。上游receipt被替换时，交易字节仍完整，比较器仍可能接受。
- **死胡同**：把“结果CSV与叶文件哈希全对”当成完整血缘证明。这只证明当前叶文件内部一致，不能证明它们来自声明的冻结预测。
- **有效路径**：继续沿链核对预测receipt、评价receipt、各自文件与源码依赖、共同数据SHA、源统计SHA及流集合，并要求串行完成/两种parity标记。加入替换上游receipt和取消完成/parity的拒绝测试。仅重跑比较统计至v2，原预测和价格路径不变；草稿v1留存。
- **通用规则**：每当报告跨版本合并结果，必须解引用并验证输入receipt的上游身份；不要把一条已记录但从未读取的SHA当成已经验证的证据。
- **牵连**：`yoyo/evaluation/spike_5r_target_comparison.py`、`tests/evaluation/test_spike_5r_target_comparison.py`、`experiments/active/exp-spike-5r-model-20260921-v5/target_comparison_v2/`。

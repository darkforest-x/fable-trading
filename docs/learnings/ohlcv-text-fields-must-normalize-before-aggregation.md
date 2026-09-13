# OHLCV 文本列必须在聚合前归一为有限数值

- **问题**：SPIKE 市场广度研究从 gzip CSV 逐行读取开发期 OHLCV 后保留了文本列；60 分钟重采样的 high、low 和 volume 因此可能按字符串语义求 max、min 或 sum，静默扭曲市场上下文。
- **死胡同**：下游特征函数以 `.astype(float)` 消费 30 分钟数据，看似会转换价格，却没有保护重采样路径；依赖后续消费者也无法在聚合已经发生后恢复正确的 high、low 和 volume。
- **有效路径**：保留原始逐行读取和前缀 digest 的字节更新顺序，在时间索引与重复 candle 冲突检查完成后，对全部 OHLCV 用 `pd.to_numeric(errors="raise")` 转换并拒绝 NaN、正无穷和负无穷。两根字符串 candle 的回归测试固定了正确的 60 分钟 high=102543.2、low=98704.6、volume=11188.567，并验证 digest 仍是原始 CSV 字节的 SHA。
- **通用规则**：凡是 CSV OHLCV 会进入 pandas 聚合，先在边界加载器完成 schema、重复和数值有限性校验；不要假定后续特征计算的转换能保护较早的聚合。
- **牵连**：`yoyo/evaluation/spike_market_breadth_study.py::_load_bars`、`tests/evaluation/test_spike_market_breadth_study.py`；开发前缀 SHA 的口径保持为 header 与截止前完整原始 CSV 记录的字节序列。

# Vectorized timestamp lookup must retain the prior duplicate rule

- **问题**：MA-profit 数据集构建为每个事件把整个来源的时间列转成 Python 字典；1m 历史可达数百万行，重复构造该字典主导了渲染时间。
- **死胡同**：直接按“源数据应唯一”改成取第一个匹配会悄悄改变旧字典的行为。旧字典在存在重复时间戳时保留最后一个索引，不能用唯一性假设替代这个语义。
- **有效路径**：对两个核心时间做向量化相等比较，并取 `flatnonzero` 的最后一个位置。在一个 1,234,264 行冻结 prefix 上，完整 `event_assets` 从约 1.61 秒降至约 0.158 秒；A/B PNG 和元数据及已解析负例均与冻结实现逐字节一致。
- **通用规则**：替换索引结构前，先写出它对重复键的精确规则；性能优化的 parity 检查要比较最终 bytes 和元数据，而不只比较索引值或计时。
- **牵连**：`yoyo/datasets/ma_profit_dataset.py` 的 `event_assets`；`tests/test_ma_profit_dataset.py`；冻结实现 `f0b54f2c20`。

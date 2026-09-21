# 派生行情的清单必须经过真实下游读取器

- **问题**：1m 聚合为2m的备用工具算对了 OHLCV，却遗漏矿工必需的 symbol/venue；随后整份继承父元数据又会把1m的 CSV SHA 和 gzip路径误写成2m属性。
- **死胡同**：只检查聚合数值或检查 JSON 有 sources，不能证明矿工能接受输出；复制所有父字段也不能保证字段语义仍成立。
- **有效路径**：保留明确的身份字段，重新计算派生路径、SHA、行数和边界；其余父属性放进 parent_source_metadata。测试把真正生成的 receipt 交给原 ma_profit_miner.load_sources，且核对父存储字段不冒充派生字段。
- **通用规则**：派生产物同时验证数值与交接契约；每个元数据字段明确属于父产物还是子产物。
- **牵连**：yoyo/data/ma_profit_two_minute_sources.py、tests/evaluation/test_ma_profit_two_minute_sources.py。仅准备备用工具，尚未生成真实2m数据或接入当前队列。

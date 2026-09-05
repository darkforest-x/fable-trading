# 当前币种横截面确认必须先做文件级历史预检

- **问题**：用当前交易所币种快照构造历史确认集时，部分新币的缓存文件完全始于 repository holdout 之后。通用分块解析器先解析首块再检查时间，因而会在没有产生收益结果前仍触碰禁止时间戳。
- **死胡同**：仅依赖 `safe_end`、小 `chunksize` 和“读取后 fail-closed”只适用于文件本身覆盖安全切点的连续历史；对 holdout 后才上市的文件，它无法在打开 OHLCV 前知道文件没有安全前缀。
- **有效路径**：利用 fetcher 写入文件名的不可变行数收据，在打开 CSV 前做保守历史长度门。当前快照冻结于 2026-09-03；少于 30,000 根 15m 的 current-cache 文件不可能同时提供安全尾段与预注册的 140 个完整日，因此直接作为数据质量不足跳过。明确命名的 preholdout archive 继续按其边界契约读取。
- **通用规则**：凡是“当前上市集合 × 历史回测”，第一步不是解析价格，而是用不打开 OHLCV 的 manifest/文件名元数据证明每个源确有安全历史；读取后报错不是零接触证明。
- **牵连**：`scripts/research_altcoin_1d_k1k2_market_context.py`、V3 `universe_manifest.json`、`confirmation_a_boundary_amendment.json`、holdout 铁律与上市/幸存者偏差。

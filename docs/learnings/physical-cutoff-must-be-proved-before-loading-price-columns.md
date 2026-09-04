# 先证明物理截断，再加载价格列

- **问题**：旧行情文件名、旧报告和来源哈希都声称数据止于 pre-holdout，但文件后来被续写到 holdout 之后；研究脚本先整文件 `read_csv`、再按时间过滤，已经读取了受保护价格。
- **死胡同**：把“计算只使用切点前的行”当成“没有读取 holdout”，或把旧报告中的时间范围当作当前文件的物理边界。文件可变时，名称、哈希记录和历史描述都不能替代当次 fail-closed 预检。
- **有效路径**：废止该运行并如实记录一次意外 holdout 访问；改用独立、物理上止于安全切点的官方归档源，先只验证时间边界和月度完整性，再加载 OHLCV；后续聚合也只从这个安全前缀生成。
- **通用规则**：任何价格研究的第一步都是验证“本次实际打开的对象”在物理上不含受保护行；如果源文件混装开发段和 holdout，必须使用不会读取越界行的分块加载器或预先生成不可变安全前缀，禁止整表加载后再过滤。
- **牵连**：`data/kline_deep/okx_BTC_USDT_SWAP_15m_158499.csv`、`yoyo/data/okx_archive_bars.py`、`scripts/optimize_btcusdtp_k1k2_intraday.py`、`experiments/active/exp-btcusdtp-k1k2-15m-5m-params-preholdout-20260904-v1/results/preflight_failure.json`、holdout 铁律 1；另见 [`whole-file-hashes-can-cross-holdout-boundaries.md`](whole-file-hashes-can-cross-holdout-boundaries.md)。

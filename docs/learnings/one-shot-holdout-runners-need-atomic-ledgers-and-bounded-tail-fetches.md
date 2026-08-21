# 一次性 holdout runner 必须先原子占账，再做有界尾段抓取

- **问题**：冻结 ETH 15m 本地深数据只到 2026-07-12，而 owner 批准的终点是 2026-08-21。
  正式 runner 既要补齐尾段，又必须保证两个并发进程不会各自消费一次、API 不会越过批准终点，
  失败也不能被伪装成“没看过”。

- **死胡同**：先读数据再写完成标记；用普通 `exists()` + 写文件占位；让通用 fetcher 从当前时间
  向后补并落盘；对分页结果 `drop_duplicates()` 后继续。这些做法分别留下竞态窗口、静默越界、
  VPS 单写者违规和 API 重复/分页错误被隐藏的问题。

- **有效路径**：所有配置、Pine hash、依赖版本、main/clean provenance 和输出路径先校验；随后用
  `O_CREAT|O_EXCL` 原子创建 `started` ledger 并 `fsync`，再允许任何数据读取。尾段第一请求固定
  `after=end_exclusive`，每页 timestamp 必须同时 `< approved_end` 和 `< cursor`，单页/跨页重复、
  未确认 candle、无 cursor 前进一律 fail-closed；只在内存与本地只读前缀拼接，不写 K 线文件。
  成功后 ledger 写 9 个产物 hash，之后只允许 `--verify-existing` 在不重开数据的情况下验真。

- **通用规则**：holdout 消费是一次不可撤销的状态转换，不是普通 batch job。它的 ledger 必须在
  第一字节数据之前原子落盘；外部补数必须从批准终点反向收敛，并把重复、未确认、边界与完整性
  当硬错误。任何失败都保留 ledger，不得自动重跑。

- **牵连**：`scripts/backtest_pine_eth_15m_v12f_holdout1.py`、
  `tests/test_backtest_pine_eth_15m_v12f_holdout1.py`、
  `experiments/active/exp-pine-eth-15m-v1/results/v12f_holdout1_consumption.json`。

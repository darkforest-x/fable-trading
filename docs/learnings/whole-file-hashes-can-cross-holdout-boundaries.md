# 整文件哈希也会越过 holdout 边界

- **问题**：行情 CSV 同时包含开发前缀与 holdout 尾部；为了记录来源身份直接执行整文件 SHA256，
  虽然没有解析价格或计算指标，仍读取了受保护区字节，违反“任何研究路径不得读取 holdout”的纪律。

- **死胡同**：把哈希误认为与数据读取无关，或认为“没有输出明文 K 线”就不算访问。哈希函数必须读取
  每个字节，文件边界不等于研究切分边界。

- **有效路径**：只通过 fail-closed 分块加载器读取 `safe_end` 之前的行，再对规范化的内存 OHLCV
  前缀计算哈希；报告明确写成 `bounded_ohlcv_sha256`，不再出现暗示整文件可安全读取的来源 SHA。
  已发生的整文件哈希访问要撤销其证据资格并如实记为纪律事件，不能用“只是一串摘要”抹掉。

- **通用规则**：含 holdout 的单文件数据，任何 `sha256sum`、`wc`、全文件压缩、复制或 schema 扫描都先
  视为读取 holdout；第一步必须调用有切点和缓冲保护的加载器，身份校验只针对截断后规范化内容。

- **牵连**：`scripts/research_pine_eth_15m.py::sha256_bounded_frame`、
  `yoyo/layers/l3_backtest/pine_allin_v7.py::load_development_frame`、
  `analysis/p1_pine_v12f_grade_a_yolo_fusion_20260903.md`、holdout 铁律 1。

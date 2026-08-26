# OKX 的 USDT-SWAP 后缀不再等于加密币宇宙

- **问题**：按 `*-USDT-SWAP`、正价格和仓库静态 blacklist 组日涨跌榜时，54 个入选品种中混入
  14 个 MRNA、SMCI、KIOXIA、UNITREE 等拟股权合约；它们形式上是 USDT 永续，却不是 Owner
  所说的“币种”。
- **死胡同**：只依赖 `BLOCKED_BASES` / `STOCKISH_BASES`。静态名字表会落后于交易所新增品种，
  这次连当前 Top20 都被新上市资产污染；继续给 blacklist 补名字仍会在下一批新合约重犯。
- **有效路径**：在读任何行情前先拉 OKX `public/instruments?instType=SWAP`，只保留
  `state=live && instCategory=1`，再与 ticker 的 `*-USDT-SWAP` 交集。现场反证中 BTC、ETH、
  PUMP、AERO、GRVT 为 `instCategory=1`，而 14 个污染项全部为 `instCategory=3`。
- **通用规则**：凡任务语义是“加密币全市场”，第一步先用交易所动态资产分类确定 universe；
  ticker 后缀只说明结算和合约形态，不能说明标的类别。发现分类漂移后，旧结果应 fail closed，
  记录行情读取但不得继续推理或悄悄改榜。
- **牵连**：`scripts/scan_15m_ma_launch_t3_daily_movers.py`、`yoyo/data/universe.py`、
  `yoyo/data/loader.py`、OKX public instruments/tickers；holdout 日榜读取需要分别记录失败配置与
  修正版配置的消费次数。

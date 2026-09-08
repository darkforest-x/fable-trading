# 公开资料如何转成可检验假说

本页是资料研究，不是推荐付费产品，也不把博主/产品宣传当作交易优势证据。

- CoinAnk 官方工具页（https://www.coinank.com/zh/tool）列出持仓变化、成交活跃程度、短周期价格振幅、资金费率及高位放量等提醒。这与 Owner 记忆中的“热门币监控”接近，但不能据此确定 Owner 当时看到的是谁。重点可学的是先发现异常，再由价格结构判断方向；提醒本身没有止损/持有/成本验证。
- Coinalyze 官方提醒（https://coinalyze.net/alerts/）支持价格、成交量、持仓、资金费率和清算条件；其自定义公式文档（https://coinalyze.net/coinalyze-custom-metrics.pdf）明确最新区间可能未完成。我们的历史比较只取已收盘/额外滞后的可用值，不直接复制当前实时公式到回测。
- CryptoCred 公开衍生品解释（https://medium.com/@cryptocreddy/comprehensive-guide-to-crypto-futures-indicators-f88d7da0c1b5）提供OI、资金费率和结构的解释框架。X搜索可见性有限，没有把第三方转载当作核实过的交易战绩。
- Coinalyze 聚合OI说明（https://coinalyze.net/blog/aggregated-open-interest/）是跨交易所指标；本轮采集仅OKX逐合约，不把两者混同。OI币本位变化与USD估值变化分开，不能把涨价本身当新增仓位。
- OKX原生逐合约OI（https://www.okx.com/docs-v5/en/#trading-statistics-rest-api-get-contract-open-interest-history）和taker（https://www.okx.com/docs-v5/en/#trading-statistics-rest-api-get-contract-taker-volume）支持4H历史，实际保留约240天。当前bucket无confirm，历史发布延迟未知。本轮采用桶起点+8H的保守可用时钟，且报告缺口。
- OKX funding history（https://www.okx.com/docs-v5/en/#public-data-rest-api-get-funding-rate-history）约3个月，actual与predicted保留分别字段，结算频率可能变化。不能拿今天预测费率回填过去。
- OKX清算WS（https://www.okx.com/docs-v5/en/#public-data-websocket-liquidation-orders-channel）并非所有清算的完整流水；没有历史完整数据，暂不把它当作已验证特征。将来实时录制也必须记录接收时间及采样限制。
- CCXT OKX统一OI历史接口（https://docs.ccxt.com/docs/exchanges/okx#fetchopeninteresthistory）与原生逐合约4H接口覆盖不同，因此本轮先调用已验证的官方原生HTTP，不为框架统一形式牺牲语义。

## 工具选择

已有pandas/NumPy足以做显式的逐根执行与时间切分；本轮不安装会改变torch/numpy契约的新依赖。Freqtrade（https://www.freqtrade.io/en/stable/strategy-101/）适合后续dry-run与lookahead/recursive辅助检查，vectorbt（https://vectorbt.dev/api/portfolio/base/）适合批量矩阵，但都不能自动解决外部数据何时可见或OHLC内部先后。NautilusTrader（https://nautilustrader.io/docs/latest/concepts/backtesting/）适合有细粒度成交/盘口数据后的执行模拟，目前仅用15m来源不值得假造tick精度。

本轮预定独立特征：相对量能、TR扩张、收盘位置、冻结箱体突破、历史BB压缩、OI4H/24H变化、主动成交方向。资金费率先做成本/拥挤背景；清算/盘口/社交热度保留为需要额外可复现数据的后续问题。特征越多不等于优势越多，需与同覆盖随机起点比较。

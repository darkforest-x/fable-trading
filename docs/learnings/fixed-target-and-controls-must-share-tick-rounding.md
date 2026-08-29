# 固定止盈与随机对照必须共用 tick 舍入

- **问题**：Python 固定 TP 按 `entry × (1±TP%)` 保存出三位小数，而 ETH 研究契约的最小 tick 是
  0.01；候选交易与匹配随机对照若各自处理，会产生不可解释的微小收益/parity 差异。
- **死胡同**：认为几十万分之一的价格差“不影响选参”而保留连续价格。它可能不改变当前冠军，却会在
  barrier touch 边界改变是否成交，并让 TradingView parity 无法逐笔对齐。
- **有效路径**：目标在开仓时按同一个 tick 规则量化，候选 replay 与 `control_outcome` 使用同一实现语义；
  用非整数 tick 的合成入场价同时断言 target、exit 和净收益。
- **通用规则**：任何新增保护单先列出价格锚点、tick 舍入、gap fill、同柱碰撞和费用五项合同，并在策略
  与对照路径各放一个相同的边界测试。
- **牵连**：`yoyo/layers/l3_backtest/pine_allin_v7.py`、
  `scripts/research_pine_eth_15m.py`、`tests/test_pine_allin_v7_backtest.py`、ETH tick 0.01。

# Pine/Python 特征 parity 不能只比公式：warmup ready 门也属于合同

- **问题**：Pine 的六线 W8 gate 显式要求 SMA/EMA 20/60/120 与滚动计数全部 ready；Python
  初版只判断 `cross_imbalance >= 0`。由于比较 NaN 会得到 false、滚动事件又可得到 0，Python
  可能在最慢 SMA120 完成前把“零净交叉”判为通过。
- **死胡同**：认为 V9 自身要约 200 根历史、所以当前交易不会落在 warmup，就把差异当成无关紧要。
  当前 ledger 可能碰巧不变，但未来信号参数、截断窗口或单元夹具一变，parity 会静默失效。
- **有效路径**：把六条 MA 非空条件物化为 `ma6_w8_ready`，long/short pass 都先 AND ready；
  测试钉住第 118 根不通过、第 119 根六线 ready 后零交叉按 threshold 0 通过。Pine 与 Python
  同时保留这个看似冗余的门，避免依赖上游信号 warmup 的偶然保护。
- **通用规则**：跨语言迁移时，特征合同至少包括价格源、窗口端点、NaN/NA 传播、min-periods、
  equality、tick rounding 和 ready gate。公式相同但初始化语义不同，仍然是 parity FAIL。
- **牵连**：`scripts/backtest_pine_eth_15m_v12_preholdout.py`、V12F/V12E、未来 Pine feature
  export 对账与截断历史回放。

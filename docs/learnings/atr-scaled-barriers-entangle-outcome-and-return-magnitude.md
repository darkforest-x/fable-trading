# ATR 障碍会把胜负概率与收益幅度缠进回归目标

- **问题**：P2-R 发现很多波动、range 和趋势特征与 taker-net return 有稳定秩相关，但无法判断它们在预测 TP/SL，还是只在预测 ATR 决定的盈亏幅度。
- **死胡同**：直接把 raw return IC 当形态 edge，会忽略 TP/SL price barrier 随 ATR 线性放大；高 ATR 下同一种失败会产生更大的 return 绝对值。
- **有效路径**：先用 `atr_at_signal / entry_price_research` 把 gross return 化成 ATR 单位，再同时检查 TP-before-SL、ATR-normalized gross 与 ATR 桶内 net IC。P2-M 复算确认 TP/SL 中位数精确为 +5/-2 ATR，20 个稳定特征里 14 个在三条控制线都衰减至少一半。
- **通用规则**：凡 target 的止盈止损由波动尺度决定，解释 feature/return 关联前必须拆开 outcome probability 与 payout magnitude；raw return IC 不能单独证明可交易形态 edge。
- **牵连**：`analysis/output/p2m_mechanism_audit_20260803.json`、`atr_at_signal`、`entry_price_research`、`gross_ret`、TP5/SL2。

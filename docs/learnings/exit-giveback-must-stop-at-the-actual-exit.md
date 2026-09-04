# 止盈回吐必须截止在实际退出时点

- **问题**：旧诊断用 `horizon_mfe_atr - realized_atr` 表示“giveback”，把交易退出后数十小时内才出现的行情也算成了止盈损失，容易错误推动更宽止损或更长持仓。
- **死胡同**：用固定未来 horizon 的最高有利价格评价 runner 捕获率。它适合描述“事后还有多少机会”，但不能回答“退出前从峰值回吐了多少”；二者混用会让每个提前退出都显得非常差，而且隐含知道退出后的未来。
- **有效路径**：同时保留两个字段：`exit_giveback_atr = mfe_at_exit_atr - realized_atr` 只衡量实际退出前的观察回吐，`horizon_opportunity_gap_atr = horizon_mfe_atr - realized_atr` 只表示事后机会差。历史冻结产物不改写，在新分析层显式更正字段名并声明旧口径不影响主选择目标。
- **通用规则**：任何 exit-quality 指标先画出信息边界；指标右端晚于 exit 就不能命名为回吐或捕获。只有 outcome label 可以看未来，退出诊断必须以真实 exit 为边界。OHLC 没有 bar 内路径时，退出 K 的 high/low MFE还只能称观察上界。
- **牵连**：`scripts/research_btcusdtp_15m_ma_runner_grid.py` 的历史 `gave_back_atr` 字段、`scripts/build_btcusdtp_15m_trend_refactor_report.py` 的修正字段，以及所有基于 MFE 的 runner / trailing-stop 报告。

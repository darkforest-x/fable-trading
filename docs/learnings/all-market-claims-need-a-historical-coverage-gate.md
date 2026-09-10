# 全市场回测先验证每个交易所的历史可得性

- **问题**：SPIKE V1 两年全市场合同要求 Binance、OKX 和 Gate 的全部 USDT 永续、30m/1H/4H/1D 一致覆盖。
- **死胡同**：先按当前 Gate 合约目录逐页请求 30 分钟 REST K 线。接口返回 `INVALID_PARAM_VALUE: Candlestick too long ago. Maximum 10000 points recently are allowed`；把窗口切小不会改变最近点数边界。官方历史下载说明列出 archive 类型，但实际 futures USDT K-line URL 对 BTC、ETH 和 0G 的多个周期均为 404，不能把文档存在当作可读数据。
- **有效路径**：先冻结目录，再对每所各取一个跨请求起点的真实分页样本，并保存原始响应与错误回执。只有每所都能覆盖要求日期、且内部缺口可解释时，才启动全量回测；否则在数据层 fail-closed。
- **通用规则**：所谓“全市场”至少分成 current-catalog universe、all-ever-listed universe 和实际可覆盖 universe。全量网络抓取前必须测量每所的历史深度和分页限制，禁止用其余交易所的成功样本填补缺失交易所。
- **牵连**：`yoyo/evaluation/spike_v1_twoyear_allmarkets.py`、`experiments/active/exp-spike-v1-twoyear-allmarkets-20260911-v1/data/`、Gate Futures K-line REST 与 historical-download 端点。

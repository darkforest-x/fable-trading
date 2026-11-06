# 零成交分钟的沿用价格也可能改变跨周期最低价

- **问题**：2026-10-31，allin3 的 GASUSDT 历史交易 291 在 1m 图返回 503，而 5m 图返回 200、459 根行情。直接调用 `strategy_backtest.candles` 复现 `minute_source_mismatch`；1011 根分钟线全部存在，202 个完整 5m 对齐桶中仅一个 low 不同。
- **死胡同**：通用提示将读取失败、超时和数据不一致都归为“首次读取可能较慢”。重试不会改变确定性的价格差异；仅忽略 open 差异也不足以处理零成交分钟，因为沿用价格可能成为聚合的 low 或 high。
- **有效路径**：逐字段对齐后定位 UTC 2026-06-28 11:25（北京时间 19:25）。该分钟 volume=0、OHLC=1.036；后续有成交分钟的最低价为 1.037。官方 Binance 当日原生 5m 归档的 open/low 都为 1.037，与保存的研究数据一致；分钟线直接聚合的 open/low 为 1.036。其余 high/close 一致，成交量差异仅为浮点求和误差。确认原因后保留现有数据和校验，未修改服务或回测结果。
- **通用规则**：跨周期差异先检查对应原始分钟的成交量、价格以及官方同周期归档。可解释的零成交价格不能直接视为真实成交，也不能通过整体放宽价格容差掩盖其他差异。未来修复应只接受能被零成交分钟完整解释的差异，并继续拒绝有成交分钟的价格冲突；诊断时区分数据冲突与网络超时。
- **牵连**：`yoyo/monitor/strategy_backtest.py`、`yoyo/data/binance_minute_window.py`；[既有首分钟 open 差异教训](empty-leading-minutes-need-explicit-ohlc-parity-evidence.md)。原始核对来源：[Binance GASUSDT 2026-06-28 5m 归档](https://data.binance.vision/data/futures/um/daily/klines/GASUSDT/5m/GASUSDT-5m-2026-06-28.zip)，分钟输入为登记的 `data/crypto/market/kline_binance_um1m_touchdays_20261027/GASUSDT/2026-06-28.csv`。未重新回测、未做内容哈希检查。

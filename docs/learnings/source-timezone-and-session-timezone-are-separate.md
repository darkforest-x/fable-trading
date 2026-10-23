# 数据时间戳时区与交易时段时区必须分开

- **问题**：2026-10-23 复用 XAUUSD 分钟数据做周一 1.618 研究时，发现旧读取器将 HistData CSV 时间戳直接 localize 为 America/New_York，夏令时期间会提前一小时。
- **死胡同**：旧代码用周日开盘/每日停盘看起来合理作为时区依据；这些现象不能替代数据供应商的时间戳定义。
- **有效路径**：先核对 [HistData 官方 FAQ](https://www.histdata.com/f-a-q/)：CSV 为不调整夏令时的 EST，OHLC 为 Bid。原始时间先按固定 UTC−5 转 UTC，再按 America/New_York 划分周一。旧历史结果保留，本次新实验用独立读取器；不复用旧 UTC parquet。
- **通用规则**：数据源编码时区和策略交易时段时区是两个契约。检查冬夏同一墙钟时间的 UTC 映射；不要根据休市时间猜测时区，也不要把 Bid-only 价格回测当成已扣真实点差的净收益。
- **牵连**：旧 `yoyo/evaluation/monday_1618_xau_spot.py:minutes`，新 `yoyo/evaluation/monday_5m_wick_engulf.py`，原始 `data/histdata/XAUUSD/HISTDATA_COM_ASCII_XAUUSD_M1_*.zip`。旧结果需要重新研究才可修正，不能静默改写历史报告。

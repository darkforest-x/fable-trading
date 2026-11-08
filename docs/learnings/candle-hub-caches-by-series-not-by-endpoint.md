# 共享K线缓存按币种+周期存，不区分接口

- **问题**：live-breadth 模拟账户需要取某个确定分钟的 OKX 1 分钟开盘价。最近的价格能取到，3 天前的取不到。
- **死胡同**：起初先请求 `/market/candles`，取不到再改用 `/market/history-candles`。单独请求 history 接口是正常的；但只要先请求过 candles，history 也会返回空。原因是 `yoyo.data.candle_hub` 按 `(origin, symbol, bar)` 一个序列存缓存，不区分接口。candles 只覆盖最近约 1440 根 1 分钟 K，旧时间段返回空，这个空结果作为该区间的回执写进了缓存；随后 history 请求同一区间时，直接拿到了缓存的空结果。
- **有效路径**：先按时间远近选定一个接口，同一分钟不再换接口重试：12 小时以内用 candles，更早的用 history-candles。修好后，当天、3 天前、6.5 天前的价格都能取到。
- **通用规则**：在共享的行情客户端上做"失败就换接口"之前，先查缓存的键里有没有接口名。键里没有，就不要在同一区间两个接口轮流试；否则第一个接口的空结果会挡住第二个，还可能让其他服务读到错误的缓存。
- **牵连**：`yoyo/monitor/breadth_paper.py`（`open_price`、`RECENT_MS`），`yoyo/data/candle_hub.py`（`fetch` 的 series key 与 receipts）。

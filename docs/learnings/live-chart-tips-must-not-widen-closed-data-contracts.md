# 实时图表的未收盘柱不能扩宽决策数据的已收盘边界

- **问题**：网页高周期只显示已收盘 K 线，视觉上慢一根；自动插针止损又复用同一个历史接口，不能为了显示实时柱而让其读取未确认 OHLC。
- **死胡同**：只缩短历史轮询间隔无效，因为服务仍过滤 confirm=0；直接把未收盘柱追加进共享 candles 会破坏止损消费者的边界。只从最新价更新 close 也无法还原原生开盘价、最高最低和成交量。
- **有效路径**：保留原 closed snapshot，新增显示专用 recent 接口，分开 closed_candles 与 forming_candle。网页订阅当前原生周期的 OKX 完整 OHLC，断线用共享短缓存 REST 补尾部。实时合并只发生在图表内，不能更新 last_close_ms；过期未确认柱直接丢弃，不能仅凭时间推进将其认定为收盘。慢 REST 用请求期间的 WebSocket revision 隔离，确认柱不得被未确认包退回。
- **通用规则**：先找共享数据的所有决策消费者，再为展示建立独立、可验证的数据边界。验证所有周期、UTC 日线、确认交接、换币换周期的旧回调、断线补数与暂停清理。渲染器复用画布并增量更新末根，历史视野和跟随视野分别保持原有行为。
- **牵连**：`yoyo/monitor/monday_candles.py`、`static/monday-candle-feed.js`、`static/monday-fib-chart.js`、`static/range-chart.js`、`monday_order_context.py`。原来的盘中触价通知有独立通道，本次没有改变其触价语义或任何执行门。

# 全局只有一个请求槽时，并发请求要排队，不能立即失败

- **问题**：2026-11-06 Owner 反馈报警回看“历史的稍微看远一点点就非常卡，K 线和信号也加载不出来”。同一页面上可能有三张图在翻历史：回看窗口、背后的 1.618 图、全屏报警壁纸。
- **死胡同**：无头 Chrome 往回拖 8 页仍是 60 帧，前端重绘不是瓶颈。如果只盯帧率和前端重算（报警射线索引、标记），会去优化一个不存在的问题。
- **有效路径**：逐个看请求的返回状态后发现两点。第一，`/api/monday-fib/history` 的服务端用 `BoundedSemaphore(1)` 加 `acquire(False)`，同一时刻第二个请求会立刻得到 `state=retry`；第二，前端 `chart-history.js` 把 `retry` 当成需要手动点“重试”的错误，之后不再分页。两者叠在一起，就是“加载不出来”。修复：服务端改为 3 个槽，其余排队最多 10 秒（OKX 的请求频率仍由共享 CandleHub 统一限流）；前端遇到 `retry` 自动退避重试（0.8/1.6/3.2/6.4 秒，最多 4 次）。另一个原因是：非主流币用的是固定时间窗，往左拖只剩空白；改为只要有本周区间就和主流币一样可以回翻。
- **通用规则**：用来限并发的锁，对交互请求应当“排队并设超时”，而不是“拿不到就失败”。客户端要把“服务端忙”和“数据错误”分开处理：前者自动退避重试，后者才提示人工处理。排查“加载不出来”时，先看每个请求的返回状态，再看渲染。
- **牵连**：`yoyo/monitor/monday_chart_history.py`（`HISTORY_SLOTS`、`SLOT_WAIT_SECONDS`）、`yoyo/monitor/static/chart-history.js`、`yoyo/monitor/static/spike-alarm.js`（`showReplayChart`）。测试：`tests/monitor/test_monday_chart_history.py::test_concurrent_history_reads_queue_instead_of_failing`、`tests/monitor/frontend_chart_history.test.cjs` 最后一条。

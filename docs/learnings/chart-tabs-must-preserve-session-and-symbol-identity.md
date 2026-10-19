# 图表切换要同时保留会话与币种身份

- **问题**：模拟开单加入区间图后，切标签不能丢 TradingView 的缩放；点击持仓币种后，区间关键位也必须跟随币种。
- **死胡同**：直接重画整个图表容器会重建 iframe。只给现有 BTC 图加一个标签，会让 ETH 或 W 的交易表单配上 BTC 关键位。初次加载还没有币种，直接调用字符串处理也会中断整个页面加载。
- **有效路径**：两个图表面板原地隐藏，币种与主题不变就不重建 iframe。复用同一 K 线渲染器；后端按币种和日界保存快照，请求只登记后台刷新需求，BTC 常驻，其余最多六个并在闲置后移除。前端用请求代次、币种、日界共同拒绝过时响应，并覆盖无币种的首次挂载。测试验证旧 BTC 响应不能覆盖新 ETH 图，浏览器实测 W 能切换且不打开日志。
- **通用规则**：为交易页面增加图表类型时，分别定义图表会话生命周期、行情身份和空状态；显示切换不能隐式重置会话，异步响应不能只按到达顺序覆盖。
- **牵连**：`yoyo/monitor/static/paper-desk-market.js`、`range-chart.js`、`yoyo/monitor/market_context.py`、`tests/monitor/frontend_paper_desk_market.test.cjs`、`test_market_context_okx.py`。

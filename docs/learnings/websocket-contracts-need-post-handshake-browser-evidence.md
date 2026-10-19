# WebSocket 契约需要握手后的浏览器证据

- **问题**：OKX WebSocket 能成功建立连接，但订阅消息里的 `id` 含连字符后返回 `Parameter id error`；HTTP、构建和单元测试都不会暴露这个错误。
- **死胡同**：把“WebSocket open”当成实时链路已成功。握手只证明传输通道存在，不证明订阅参数被服务端接受，更不证明收到了 candle push。
- **有效路径**：按官方契约把订阅 ID 限制为字母数字，并在真实浏览器等待订阅响应与首条 candle 数据；只有界面状态从 connecting/polling 进入 live 才算通过。
- **通用规则**：实时流验收必须分成 connect、subscribe-ack、first-data、reconnect/fallback 四个阶段；不能用其中任何一个阶段代替全链证据。
- **牵连**：`prototypes/fable-signal-console/src/LiveMarketChart.jsx`、OKX `/ws/v5/business`、25 秒内心跳、REST 轮询降级、浏览器 QA。

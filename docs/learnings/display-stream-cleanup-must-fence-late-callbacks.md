# Display stream cleanup must fence late callbacks

- **问题**：手机切页或进入后台时，旧行情连接关闭后仍保留 onmessage/onopen/onerror，晚到的消息可能重新排入渲染定时器。仅有 pong 也可能让旧价格长时间驻留。
- **死胡同**：只清掉当前 timeout 并调用 close 不够；事件已排队，旧回调仍可以创建新的 timeout。降低刷新频率也不会解决连接所有权问题。
- **有效路径**：每个回调检查当前连接身份与 active 状态，停止时解除全部事件并清除订阅、心跳、连接超时和合并更新任务。价格有效期独立于传输层 pong；100ms 合并窗口保留。模拟旧回调、突发行情、断线重连和静默连接验证。
- **通用规则**：高频数据首先明确资源归属与失效边界，再讨论降低刷新率；传输连接活跃不等于业务数据新鲜。
- **牵连**：`yoyo/discipline_app/mobile/src/quoteFeed.ts`、`useLiveQuotes.ts`、`tests/discipline_app/quote_feed.test.ts`。只影响公开显示行情，不替代服务端执行状态。

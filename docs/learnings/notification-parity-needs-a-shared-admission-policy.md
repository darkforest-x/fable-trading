# 通知渠道对齐必须共用准入规则，同时保留独立回执

- **问题**：周一 1.618 的 Telegram 已按原始评分严格大于 0.4 过滤并采用三行文案，Bark 仍在触价入队后直接发送长消息。
- **死胡同**：只复用短文案会继续推送低分事件；复制一套评分判断容易再次漂移。用 Telegram 的发送成功状态决定 Bark 是否发送，则会把两个渠道的开关、限流与网络结果错误地绑定。
- **有效路径**：两个发送器共用同一事件身份的只读评分入口、阈值、评分等待与新鲜度复检，以及三行文案。Bark 在评分合格后才原子认领自己的队列；等待评分不消耗 HTTP 尝试次数，结束等待的写回还须校验状态和订阅代次。TG 与 Bark 各自保留切点、重试与不确定回执。
- **通用规则**：对齐渠道时先区分“共享的通知资格”和“独立的投递结果”，用同一组阈值边界测试两个真实发送入口，并检查等待、关闭、过期与并发认领。
- **牵连**：`yoyo/monitor/monday_fib.py`、`monday_fib_bark.py`、`monday_fib_service.py`、`server.py`。保留原 90 秒新鲜度与每周每侧首次触及，不重发历史或未知结果。参见 [异步评分的事件身份](score-gated-alerts-need-event-identity-and-pending-state.md) 与 [渠道独立状态](notification-channels-need-independent-delivery-state.md)。

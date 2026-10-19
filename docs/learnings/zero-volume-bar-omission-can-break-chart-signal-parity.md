# Zero-volume bar omission can change signal admission without changing the current OHLC

- **问题**：2026-10-15 北京时间02:00确认的 APP 1H 空头在前端出现，但加载 SPIKE V13.1 后 TV 对应01:00 K线的确认方向为0；该根 OHLC 与 API 完全一致。
- **死胡同**：只比最新价格、合约和指标版本不足以解释差异。TV CSV导出最后被当前Basic方案限制；没有宣称取得完整TV数据。尝试省略全部33根零成交行时，旧诊断内核对单根分段报长度异常，未拿失败运行作证据，也未修改生产内核。
- **有效路径**：TV数据窗口逐根显示2026-10-12 04:00之后直接为06:00；API保存05:00的零成交平价K线。仅删除这一个已观察缺失行做敏感性重放：1532行/0缺口时原锚点v7/v9通过；1531行/1缺口时v7_ready=false、v9_reason=not_v7，原锚点无法建立。因果机制是缺口重置BB历史预热；完整跨源逐笔一致性仍未认证。
- **通用规则**：比较信号前先比较时间轴和零成交K线政策。不得为贴合某张图静默删除交易所原始K线，或用回补假K线掩盖TV缺失。先保留两边数据口径并提供逐笔核对信息，再决定是否建立独立的图表一致性计算路径。
- **牵连**：`yoyo/monitor/static/app.js`、`yoyo/monitor/v9_signals.py`、`yoyo/evaluation/spike_v7_fast.py`；本地只读证据位于 `/Users/zhangzc/Documents/Codex/2026-10-15/notion-48-51/app-zero-volume-diagnosis.json`，检查点为同目录 `app-1h-checkpoint.json`。本次没有改信号规则、通知门或订单设置。

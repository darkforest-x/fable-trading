# V1 chart markers must accept the current protocol kind

- **问题**：实时 V1 图表的原始事件使用 `spike_burst_v1`，而浏览器绘图仅筛选历史名称 `tv_start`；回放中的旧箭头还能显示，实时箭头会被静默丢弃。
- **死胡同**：把 YOLO 的 `indicator.kind` 强制改写成 `tv_start` 只让确认卡的父箭头偶然可见，不能覆盖图表事件中的真实 V1 原始记录，也混淆了后端事件身份。
- **有效路径**：绘图层同时接受当前 `spike_burst_v1` 和已持久化的 `tv_start`，并直接使用 YOLO 记录携带的原始指标。绘图仍使用同一 `bar_open_ms` 和方向；没有改变信号、模型、通知或事件存储。
- **通用规则**：显示层枚举协议事件时，应兼容当前受支持的协议 kind 与明确保留的历史 kind；不要通过重写嵌套事件来修复可视化。
- **牵连**：`yoyo/monitor/static/app.js`、`tests/monitor/frontend_cards.test.cjs`；当前后端常量为 `yoyo.monitor.SIGNAL_KIND = "spike_burst_v1"`。

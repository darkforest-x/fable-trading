# Review-chart markers must distinguish bar open from confirmation close

- **问题**：本地图册把确认收盘时间直接交给以 K 线开盘时间索引的 Lightweight Charts，原始 V1 箭头因此落到下一根 K 线。
- **死胡同**：把同一个 `signal.time_ms` 同时用于箭头、近景和初始止损，表面上统一了时钟，实际混淆了图表坐标与信号可用时点。
- **有效路径**：保留 `signal_bar_open_ms` 给原始箭头、竖线和近景；只让 `signal_close_ms` 作为确认事实与初始 SL 的起点。用可执行纯时间契约测试两者优先级。
- **通用规则**：渲染前先确认横轴存的是 bar open 还是 bar close；同一记录的显示坐标和可用时点必须分别命名、分别测试。
- **牵连**：`yoyo/evaluation/static/spike_v1_review/timing.js`、`app.js`、本地冻结 JSON 契约。

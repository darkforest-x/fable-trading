# K 线开盘戳不能直接写成信号可用时间

- **问题**：行情表通常用 bar 的开盘时间命名；对只允许读取已收盘 K 线的模型，`window_end_time` 是最后一根输入 K 的开盘戳，不是模型能知道该输入的时刻。
- **死胡同**：在图上直接把 `window_end_time` 标成 `first available`。15m 时只差 15 分钟不易察觉，换到 4h 就会把可见时间提前整整 4 小时，形成展示层前视。
- **有效路径**：同时保留 `*_bar_open_time` 与 `*_available_at`；后者严格等于开盘戳加 timeframe delta。虚线仍画在输入右端那根 K 上，但标题和报告只用收盘后的 `available_at` 描述首次/最后可见时间。
- **通用规则**：凡输出“检测时间、信号时间、首次可见”，第一步确认源时间戳代表开盘还是收盘；若模型要求 confirmed bar，用户可行动时间必须是 `bar_open + timeframe`，不能靠字段名猜。
- **牵连**：`scripts/scan_4h_ma_launch_yolo_latest.py`；4h `BAR_DELTA`；`first_detection_bar_open_time` / `first_available_at`；项目无前视纪律与 tip 新鲜度口径。


# K 线开盘标签不是模型可用时间

- **问题**：15m 检测账本的 `window_end_time` 存的是最后一根 K 线的开盘时间。把
  `01:15` 直接报成“01:15 已检出”，会在 15m 周期上凭空夸大 15 分钟提前量。
- **死胡同**：只在图上标 `window_end_time`，或把下一根 K 的 open 当成同一时刻，都会混淆
  “最后一根输入叫什么”和“它什么时候已经收完”；对截图当下未收盘 bar 直接
  重建，还会把后来才知道的 high/low/close 混入输入。
- **有效路径**：同时记录三个时间：`bar_open_time` 作为账本标签，
  `available_at = bar_open_time + timeframe` 作为无前视可用时间，以及截图当下的
  `cutoff` 作为排他上界。输入只保留 `bar_open_time + timeframe <= cutoff` 的已收盘 K；
  交付同时标“01:15 bar”和“01:30 完整可用”，点位分别报该 bar close 与下根 open。
- **通用规则**：任何基于 OHLCV 完整 bar 的检测、特征或信号，第一步先确认时间戳
  表示 open 还是 close。若是 open-time 语义，所有延迟、新鲜度和入场计算都必须用
  `available_at`，禁止直接用原时间戳声称已可执行。
- **牵连**：`scripts/scan_15m_ma_launch_t3_daily_movers.py` 的 `window_end_time`、
  `scripts/scan_15m_ma_launch_owner_yolo_recent5d_rawbox.py` 的首次 episode 代表规则、
  15m 新鲜度预算、截图回放 cutoff，以及任何 Telegram / forward 对外时间标注。

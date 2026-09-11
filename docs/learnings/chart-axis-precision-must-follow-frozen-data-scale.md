# Chart-axis precision must follow the frozen data scale

- **问题**：Lightweight Charts 默认两位小数会把小币 K 线与 MD/SB 都显示为 `0.03` 或 `0.00`，六条 MA 的末值标签还遮挡价格轴。
- **死胡同**：把统一的两位格式应用到所有合约，价格事实虽未变，视觉读数却失去区分度。
- **有效路径**：从本笔冻结 OHLC 和 MD/SB 的最小非零量级推导 `priceFormat`，限制到 2–12 位；MA 不显示末值标签，K 线和 MD/SB 保留。
- **通用规则**：图表精度必须从所绘数据推导，并以范围限制防止浮点噪声扩大读数；不能用展示舍入掩盖原始价格。
- **牵连**：`yoyo/evaluation/static/spike_v1_review/format.js`、Lightweight Charts v4.2 本地复盘页。

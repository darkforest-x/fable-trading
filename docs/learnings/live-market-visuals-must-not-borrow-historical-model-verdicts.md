# 实时行情图不能借用历史模型裁决

- **问题**：同一页面需要展示实时 K 线和历史模型回放；如果直接用实时图替换回放图，右侧旧裁决会看起来像是对当前行情作出的。
- **死胡同**：把 K 线画得“像 TradingView”后继续沿用原检测框、时间和裁决布局。视觉连续性反而掩盖了事件不对齐，形成一条不存在的实时推理链。
- **有效路径**：把 `实时行情` 与 `模型回放` 做成显式双模式，使用独立 API 和状态；实时模式反复声明“未送入模型”，回放模式保留 exact pixels、事件 ID 与历史时间。
- **通用规则**：任何页面同时出现 live data 与 offline verdict 时，第一步先画清数据血缘；只要事件 ID 无法相等，就必须在交互、文案和代码接口三层隔离。
- **牵连**：`prototypes/fable-signal-console/src/App.jsx`、`LiveMarketChart.jsx`、`/api/market/*`、`/api/model/*`、holdout 与 forward/execution 边界、ADR 0005。

# 实时检查图必须完整复用渲染器的均线合同

- **问题**：实时行情图只画了 EMA20、SMA60、SMA120 三条代表线，但 L1 视觉输入的真实合同是 SMA/EMA 20、60、120 共六条；看板因此无法如实呈现模型所观察的均线束。
- **死胡同**：把图例中的“20 / 60 / 120”理解成每个周期任选一条均线，会让页面看起来更简洁，却丢掉 SMA 与 EMA 的相对位置和交叉信息。仅设置 `lastValueVisible=false` 也不足以消除旧实例里带标题的轴标签，必须清空 series title 并在完整重载后验证。
- **有效路径**：先从 `yoyo/layers/l1_detection/render.py`、`yoyo/layers/l1_detection/data.py` 和共享 `chart_theme.js` 反查准确的六线名称、算法与颜色，再用单一前端配置同时生成图例、初始数据和实时更新；均线系列关闭最新值、标题和十字标记标签，只保留顶部图例。
- **通用规则**：任何声称与检测器视觉语义一致的检查图，第一步都应读取渲染合同，而不是根据界面缩写猜指标；改动后必须硬刷新浏览器，不能只看热更新后的旧图表实例。
- **牵连**：`prototypes/fable-signal-console/src/LiveMarketChart.jsx`、`prototypes/fable-signal-console/src/App.jsx`、`prototypes/fable-signal-console/AGENTS.md`、`docs/decisions/0005-separate-live-market-visualization-from-model-replay.md`；实时行情仍仅展示，不进入 L1/L2、forward 或执行器。

# 共享图表在后台标签里不取数，报警壁纸要在页面重新可见时补刷新

- **问题**：全屏报警的壁纸 K 线用 `SpikeMondayFibChart.show()`。在 Chrome 里打开时整屏是黑的，只显示“正在加载区间 K 线…”。
- **死胡同**：先怀疑模拟接口和 LightweightCharts 没加载：直接调用接口能拿到 720 根 K 线，库也能加载。之前在隐藏的内置浏览器里又遇到过另一种现象（自动翻页卡住），原因是后台页面不执行 `requestAnimationFrame`。两件事看着像，原因不同，不能混为一谈。
- **有效路径**：`monday-fib-chart.js` 的 `refresh()` 开头写着 `if(!target||controller||document.hidden)return;`：页面隐藏时直接跳过，而且之后不会自己补一次。标签在后台载入时就卡在加载状态。报警恰恰常在后台标签触发，所以在 `visibilitychange` 变为可见时，对已打开的报警壁纸和回看图各调一次 `retry()`。
- **通用规则**：复用一个“只在可见时工作”的组件时，先确认它变回可见后会不会自己恢复。不会的话，由调用方在 `visibilitychange` 里补一次刷新。验收这类组件要在真正可见的页面里做，隐藏的测试面板会同时掩盖取数和动画两类问题。
- **牵连**：`yoyo/monitor/static/spike-alarm.js`（visibilitychange 处理）；`yoyo/monitor/static/monday-fib-chart.js::refresh`；`yoyo/vision_research/static/chart-tools.js::frame`（基于 requestAnimationFrame 的节流）。相关：[本机监控直接服务工作区](local-monitor-serves-the-working-tree-live.md)。

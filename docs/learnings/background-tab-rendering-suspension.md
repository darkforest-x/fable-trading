# 后台标签页会暂停 ResizeObserver 和 rAF——图表初始化要能自愈

- **问题**：Lightweight Charts 图表画布卡在 300×150 默认尺寸、`fitContent`/
  `setVisibleRange` 全部失灵，且"有时好有时坏"无法稳定复现。
- **死胡同**：手动 `chart.resize()`、自管 ResizeObserver、rAF 延迟定位——
  全部无效，因为它们和图表库内部机制依赖同一个被暂停的东西。
- **有效路径**：在页面里直接实验证明 ResizeObserver 回调数为 0——
  **不渲染的页面（后台标签、锁屏、无头预览面板）会暂停 RO 和 rAF**。
  图表在这种状态下创建就永远是默认尺寸。修复组合拳：
  ① `autoSize: true`（库自带的 RO 会在页面可见时自动补课）；
  ② 定位用 `setTimeout` 不用 rAF（定时器在后台仍然触发）；
  ③ `subscribeSizeChange` 里重放最后一次定位（尺寸补课时位置也跟着补）。
- **通用规则**：任何"页面可能在后台被打开/刷新"的图表 UI，初始化都不能假设
  创建时刻能拿到真实尺寸；所有一次性布局动作都要有可见时的重放路径。
  调试"尺寸/布局怪病"时先测 RO 是否活着，再怀疑代码。
- **牵连**：`src/webapp/static/app.js`（makeChart/ensureKlineChart/focusMarker）；
  静态资源另加了 no-cache 头，防止旧 JS 掩盖修复。

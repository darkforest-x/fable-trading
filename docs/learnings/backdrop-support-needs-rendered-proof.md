# 背景模糊的 CSS 支持不等于测试浏览器实际渲染了模糊

- **问题**：iPhone WebKit 验收时，`backdrop-filter` 和 `-webkit-backdrop-filter` 的计算值均为 `blur(14px)`，截图却透出清晰文字和 K 线；不能据此宣称 iPhone 毛玻璃视觉通过。
- **死胡同**：临时取消根节点 `isolation` 和面板 `transform` 没有改善；改用 headed WebKit 仍能复现。这些猜测性 CSS 修改只留在测试页面，没有写入应用。
- **有效路径**：在同一 WebKit 中建立独立红蓝条纹背景和一个带标准／前缀 `backdrop-filter` 的固定透明块，截图中的条纹仍未模糊。故证据支持测试环境存在渲染限制，不能直接归因于应用的叠层。Chrome 正常渲染同一应用的玻璃；WebKit 负责验证真实触摸事件、可见性、布局和计算透明度，真机材质仍明确未验证。
- **通用规则**：视觉效果与声明分开验收。遇到支持检测通过但像素不符合预期，先在同一引擎做最小渲染对照，避免为测试环境问题污染产品 CSS。
- **牵连**：`output/playwright/chart-focus-20261027/webkit-minimal-backdrop.png`、`webkit-glass-probe.png`、`webkit-sheet-settled.png`，以及 `docs/design/stitch-20261027-chart-focus/` 的材质规格。产品保留 Safari 前缀、透明度 16%–24% 和 14px 模糊，无模拟器专用补丁。

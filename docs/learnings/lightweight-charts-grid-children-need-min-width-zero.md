# Lightweight Charts in CSS grid needs min-width zero

- **问题**：P2-10 手机看盘适配时，桌面加载过 K 线图后切到 390px 视口，页面横向溢出 556px；`#kline-chart` 仍保持约 902px 宽。
- **死胡同**：只改媒体查询里的 grid 列数不够。`.signals-layout` 已经变成单列，但 grid 子项默认 `min-width:auto`，Lightweight Charts 的画布最小内容宽度仍会把列撑成桌面尺寸。
- **有效路径**：用真实浏览器量 `scrollWidth`、grid column 和 chart/panel rect，确认是 grid item min-content 撑开；给 `.panel`、grid 子项、`.chart`、`.table-wrap` 明确 `min-width:0`，让 ResizeObserver/autoSize 能按手机列宽重算。
- **通用规则**：图表、表格、canvas 放进 CSS grid/flex 容器时，移动端第一项检查不是列数，而是所有中间容器是否允许收缩：`min-width:0` 必须从 grid 子项一路传到实际图表容器。
- **牵连**：`src/webapp/static/style.css`、`src/webapp/static/app.js`、Playwright 390px mobile QA。

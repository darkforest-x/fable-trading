# 图标 SVG 的全局描边不能继承到数据图表

- **问题**：资金曲线坐标数字显得粗糊，图表字号在手机上又被 viewBox 缩得过小。底部原生 range 控件占用一整行，与交易页的紧凑风格不一致。
- **死胡同**：只降低文字 font-weight 无法解决，因为 `styles.css` 全局 `svg { stroke:currentColor; stroke-width:1.5 }` 给文字额外描边。只改 viewBox 尺寸也会同步缩放线条、文字和命中区域。
- **有效路径**：数据 SVG 显式重置 fill/stroke，曲线和网格独立声明描边；坐标文字用 HTML 定位，保持稳定 CSS 字号。展示真实余额阶梯与权益缺口，分段面积填充不跨缺口。移除可见 range，使用图上指示线和提示卡，并为同一图面保留 slider 语义、方向键/Home/End 与触屏点选。指针仅更新提示节点，缓存几何计算，刷新后保留键盘焦点。
- **通用规则**：图表文字异常加粗，先查继承的图标样式；响应式图表要分别处理绘图区、标签和交互，不要让 viewBox 统一缩放所有文字。美化不得改写数据或把缺失行情连起来。
- **牵连**：`yoyo/monitor/static/paper-desk-analytics.js`、`paper-desk.css`、`paper-desk.js`；回归 `tests/monitor/frontend_paper_desk_analytics.test.cjs`；浏览器截图及检查 `output/playwright/task54/curve/`。全站图标默认值保持原样，修复限定在资金图表。

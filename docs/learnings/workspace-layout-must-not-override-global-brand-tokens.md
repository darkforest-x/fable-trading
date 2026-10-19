# Workspace layout variants must inherit one application theme

- **问题**：市场工作台单独覆盖全局颜色变量，切换到信号页时 Logo、导航、背景和控件一起变色；Owner 在 2026-10-18 明确要求首页使用信号页主题。
- **死胡同**：此前把不同 Stitch 画板的配色差异直接实现成页面级主题，虽然局部忠于原图，却破坏了同一产品的连续性。只修导航颜色也会留下图表 Canvas 的独立硬编码。
- **有效路径**：去除市场页品牌与表面色变量覆盖，布局继续独立；控件引用语义 token，Canvas 从实际容器读取公共图表色，颜色也进入渲染更新键。浏览器切换路由后核对实际计算样式。
- **通用规则**：设计画板有差异时，先区分共享品牌、布局变体与数据语义色；同一应用的路由不得顺手重定义品牌主题。Canvas 也属于主题消费者。
- **牵连**：`market-workstation.css`、`market-terminal.js`、`design-tokens.css`、根 `DESIGN.md`。六条均线的系列颜色和交易计算不随品牌主题改动。

# Generated audit grids need fluid minmax

- **问题**：P2-11 打标审计页在 390px 手机视口横向溢出 114px，虽然桌面页面和图片数量都正常。
- **死胡同**：只看生成脚本是否成功、图片是否嵌入不够；`repeat(auto-fill,minmax(480px,1fr))` 在桌面合理，但移动端会把列宽硬撑到 480px。
- **有效路径**：真实浏览器量 `scrollWidth - innerWidth`、grid 模板列和 figure 宽度，确认是静态 HTML 的 grid 最小列宽问题；把列定义改为 `minmax(min(480px,100%),1fr)` 并给页面启用 border-box。
- **通用规则**：生成给 owner 审阅的静态 HTML，也要跑手机宽度；任何 `minmax(<fixed px>,1fr)` 都要先问固定最小值是否会超过窄屏内容宽。
- **牵连**：`scripts/label_audit.py`、`src/webapp/static/label_audit.html`、Playwright 390px QA。

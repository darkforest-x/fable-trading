# 横向滚动容器也必须约束绝对定位的辅助标签

- **问题**：390px移动页面的可见表格已局部滚动，document却报告695px总宽。
- **死胡同**：只检查表格的overflow-x:auto和截图，会漏掉表头中绝对定位的visually-hidden标签；它的包含块在滚动区之外。
- **有效路径**：检查越界元素的DOM矩形，给table-scroll建立position:relative包含块；同一浏览器复测页面scrollWidth从695降至390，表内滚动仍保留。
- **通用规则**：响应式验收同时量document宽度与局部滚动宽度；辅助访问元素也属于布局，不能只看可见像素。
- **牵连**：yoyo/rotation/web/styles.css；390px真实浏览器验收。

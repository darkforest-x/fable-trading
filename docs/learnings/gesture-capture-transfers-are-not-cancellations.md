# Pointer capture transfer must preserve the active gesture

- **问题**：底部菜单从按钮开始拖动时，第一段能跟手，第二段却突然回弹；原版松手也先清零，再用 16px 动画替代真实位移。
- **死胡同**：将所有冒泡的 `lostpointercapture` 当作取消。触屏按钮的隐式捕获转交给导航容器时，旧按钮也会发出该事件；它并不表示新容器失去手势。
- **有效路径**：方向锁定后才捕获指针；仅当事件来自当前捕获拥有者、且它确实不再捕获该指针时取消。图表的第二指不创建导航动画。松手从实际位移连续退场，在屏外换内容后进场；重抓读取正在显示的位置并取消旧完成回调。
- **通用规则**：手势取消必须核对事件来源、指针身份和当前捕获拥有者。真实触屏事件的中途位移与最终状态都要测，不能只测纯函数或最终选中的菜单。减少动态效果仍应逐像素跟手。
- **牵连**：`useSwipeNav.ts`、`navigation.ts`、玻璃样式、`tests/discipline_app/navigation.test.ts`；Stitch `f5d3f95247be479983f598ecb45c032b`。Chrome 390px 记录 -20/-50/-80px 同步位移、30px 回弹、100px 边界位移衰减为35px，图表平移/双指缩放不切页；证据 `output/playwright/app-glass-0.9.1/checks.json`。不据桌面模拟宣称真机帧率。

# Icon size must use visible glyph bounds

- **问题**：相同图标画布中的 `$` 与 `S` 仍显得比邻近 App 小；按 CSS 字号生成的设计稿声称 75%，但不同字体的实际轮廓并不服从这个比例。
- **死胡同**：把 font-size 当成图形高度，或者只放大整个带留白的 PNG，都不能稳定控制视觉占比；网页 favicon 也不等于 iOS 主屏幕专用图标。
- **有效路径**：保留已确认的矢量轮廓，按真实外框高度归一化到 768/1024，以实际轮廓中心定位。输出后检查白色像素边界与不透明背景；Android 自适应前景另外保留平台 overscan；网页显式提供 apple-touch-icon。
- **通用规则**：图标大小按可见形状测量，不能按字框测量；源资产交付和设备已安装图标刷新是不同的验收项。
- **牵连**：`yoyo/discipline_app/scripts/build_icons.mjs`、`design/icon-size-0.8.16/source.json`、`yoyo/monitor/static/index.html`。已有 iOS 网页快捷方式的图标更新需在用户设备另行核实。

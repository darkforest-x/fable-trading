# 分页 TabView 里的全屏 K 线要关掉翻页，拖动才归图表

- **问题**：原生纪律 App 2.1.0 的 K 线在模拟器里能拖，owner 真机反馈“K线为什么不支持拖动”。页面放在 `TabView(.page)` 里，左右翻页与图表横向拖动抢同一个手势。
- **死胡同**：只靠图表上的 `DragGesture(minimumDistance: 4)`（普通优先级）——模拟器里赢，真机上翻页的 UIScrollView 平移常常先识别，图表收不到或被取消；只做横向平移也和工作台 freePan（横竖都能拖）的手感不一致。
- **有效路径**：① 图表手势改 `highPriorityGesture`，最小距离 2，同时处理 x/y 两个方向，惯性用 `predictedEndTranslation`；② 1.618 页本身就是全屏图表，在 TabView 上 `.scrollDisabled(tab == .fib)` 关掉翻页（网页版也给这一页加了 `data-no-swipe`）；③ 价格轴拖拽、双击复位、捏合缩放作为并行手势挂上。模拟器斜向拖动后图表平移、页面不切换。
- **通用规则**：在可翻页或可滚动容器里放可平移内容时，先决定这块区域的手势归属：整页都是可交互画布就关掉容器的滚动；局部画布用高优先级手势。手势结论必须在真机上确认，模拟器的触摸时序比真机宽松。
- **牵连**：`yoyo/discipline_app/native/SpikeDiscipline/Fib/CandleChart.swift`、`App/SpikeDisciplineApp.swift`（`.scrollDisabled`）；参考 `yoyo/monitor/static/range-chart.js` 的 `wallpaperInteraction`。

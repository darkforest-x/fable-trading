# 模型框叠加必须经过图表数据坐标

- **问题**：YOLO 返回的是 exact 图片归一化像素框，而交互图增加了坐标轴、时间轴和缩放状态；直接用图片百分比或未经验证的 logical index 会让框偏位，甚至压缩成左侧一条竖线。
- **死胡同**：把小数 bar index 直接传给当前 Lightweight Charts 的 `logicalToCoordinate()`，实际浏览器中两端都得到 `0px`；固定 CSS 百分比则在拖拽、缩放后必然与 K 线脱离。
- **有效路径**：服务端用 canonical `ChartTransform` 将像素框反解成 fractional bar/price 边界；浏览器用相邻真实 K 线的 `timeToCoordinate()` 做横向插值，用 candle series 的 `priceToCoordinate()` 投影纵向，并在可见范围和容器尺寸变化时重算。
- **通用规则**：跨两个渲染器叠加标注时，必须先回到共同的数据坐标系，再各自投影；最终验收要读取实际 DOM 坐标并截图，不能只确认响应里“有框”。
- **牵连**：`yoyo/console/replay.py::_chart_box_geometry`、`ReplayMarketChart`、Lightweight Charts 的时间/价格坐标 API；框仅为同一历史推理结果的可视化，不是重新检测。

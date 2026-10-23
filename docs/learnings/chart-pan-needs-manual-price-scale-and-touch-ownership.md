# K 线拖动要同时验证价格轴状态和浏览器手势归属

- **问题**：周一 1.618 全屏图表设置了 `pressedMouseMove`，横向能移动，纵向仍不动；手机还继承普通页面的 `pan-y pinch-zoom` 和关闭纵向触摸的配置。
- **死胡同**：上一轮验证了浮层拖动和键盘缩放，把交互选项已开启当成画布手势可用。实际纵向拖动 120px 后，四条价位线仍在原位。只检查配置或按钮不能覆盖图表内部的状态门。
- **有效路径**：在真实浏览器先拖价格轴，再拖画布，纵向平移立即恢复。核对固定的 [Lightweight Charts v4.2.0 源码](https://github.com/tradingview/lightweight-charts/blob/v4.2.0/src/model/price-scale.ts)：`startScroll` / `scrollTo` 在 `autoScale` 开启时直接返回。全屏图表在画布主指针按下的捕获阶段解除自动缩放，后续手势仍由原生图表处理；坐标轴、链接和浮层不走此逻辑。全周、最新及 Home/End 恢复自动价格范围，轮询保留手动视角。全屏页单独接管触摸，普通可滚动工作区保持原配置。
- **通用规则**：验收图表必须分别实际拖动画布、价格轴、时间轴，并验证缩放、复位和刷新后的视角。选项开启、键盘生效、浮层可拖动，均不能替代画布手势证据。窄屏鼠标测试与触摸事件单测不能宣称手机真机双指验收。
- **牵连**：`yoyo/monitor/static/range-chart.js`、`monday-fib-chart.js`、`monday-fib.css`、共享 `chart-tools.js`。本轮 Stitch `2ef0866509164e07a536813ee3944d51`；复现和修复截图在 `artifacts/monday-fib-chart-interaction-20261023/`。真实数据、四条实线及服务状态保持原语义。

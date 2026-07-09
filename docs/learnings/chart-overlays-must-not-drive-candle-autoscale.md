# 图表标注层不要驱动主 K 线缩放

- **问题**：信号页在同一币种内快速切换成交单时，K 线会被推到可视区外，表现为蜡烛消失、价格轴范围仍像停在上一笔交易。
- **死胡同**：只检查 K 线数据是否还在、或者只看成交量副图是否正常，会误以为是渲染偶发问题；实际异常来自 entry→exit path 这类辅助线仍挂在右侧价格轴上，参与了主 K 线 autoscale。
- **有效路径**：把辅助 path 的 `autoscaleInfoProvider` 置空，让它不参与右轴缩放；每次 focus 新成交单前先清空旧 path，再显式恢复右侧价格轴 `autoScale`。验收必须连点多个入场价差异大的成交单，并用真实浏览器检查蜡烛像素和 entry/exit 坐标。
- **通用规则**：Lightweight Charts 里凡是说明性 overlay（路径线、色带、背景区域）都应优先放到独立 scale 或禁用 autoscale；主价格轴只让真实 K 线和必要价格线决定。
- **牵连**：`src/webapp/static/app.js` 的 `pathSeries`、`focusMarker()`、右侧 price scale autoscale、VPS 真实 Chrome 验收。

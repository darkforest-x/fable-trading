# SwiftUI Canvas 不会插值状态，K 线惯性必须逐帧驱动

- **问题**：owner 2026-11-09 反馈原生 App 的 K 线“拖动起来还是有卡顿的感觉”，要像 TradingView 一样流畅。`CandleChart` 头注释写着“pan … with momentum”，看起来已经有惯性。
- **死胡同**：松手时用 `withAnimation(.smooth(duration: 0.55)) { offset = target }` 做惯性。可 `offset` 只是画布闭包里用的 `@State`，Canvas 没有可插值的属性，SwiftUI 只会用终值重画一次，所以松手那一帧图表直接跳到终点。代码读起来像有动画，实际并没有，单看代码看不出来。
- **有效路径**：先加一个 DEBUG 帧探针（`-SpikeQAFrames YES`：用 CADisplayLink 记录帧间隔，再统计每次 Canvas 绘制的 CPU 时间），在隔离副本、独立模拟器上用同一组手势测改前和改后。然后改三处：① 惯性改成解析式指数衰减（UIScrollView normal，约每秒衰减到 e⁻²），用 `TimelineView(.animation(paused: glide == nil))` 逐帧算位置，新触摸先停住；② K 线（影线 + 实体）和成交量按涨 / 跌 / 周一三种颜色各合成一条路径，边缘对齐设备像素，一帧从“每根 1 次描边 + 2 次填充”降到最多 6 次填充；③ 格式化字符串、往周首触标记和均线序列做缓存，SMA 去掉 `Array.removeFirst` 的 O(n·p)。
- **实测**（iPhone 17 Pro Max 模拟器，60Hz；先双指缩小，再慢拖 70 点，再甩动；-O 构建）：慢拖掉帧 6/132 → 1/130，每帧绘制 2.88ms → 1.52ms（p95 4.06 → 2.59ms），双指缩放 3.33 → 1.13ms；松手从“一帧跳到终点”变为惯性滑动，多数滑动 0–2 帧掉帧。Debug（-Onone）下慢拖掉帧 41 → 10。约 7 次甩动中有 2 次在加载更早一页 K 线前后出现 270–340ms 停顿，挂上 Time Profiler 后没复现，原因未定。
- **通用规则**：用 Canvas / Path 自绘的图表，`withAnimation` 改它的输入状态等于没动画。要有过渡，就得逐帧驱动（TimelineView / CADisplayLink）。流畅度先测再改：同一组手势、同一台模拟器，比较改前改后的掉帧数和绘制耗时。不要凭感觉说“已经有惯性”或“已经很顺”。
- **牵连**：`yoyo/discipline_app/native/SpikeDiscipline/Fib/CandleChart.swift`（1.618、合约、横屏三处共用）。第二档（UIKit 自绘层 + 原生手势 + 按需重绘）尚未做，模拟器数字也不能代替 120Hz 真机。

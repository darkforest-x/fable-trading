# 图表性能缓存必须按输入字段失效

- **问题**：K 线已有 requestAnimationFrame 合帧和增量 series.update，但同一根 K 线的光标更新仍反复格式化并替换六均线 DOM；实时尾 K 每次变化又重算全部历史均线。长历史交易标记还逐根扫描成交时间。
- **死胡同**：只检查画布是否重建、或只合并事件到每帧，发现不了帧内重复的 DOM 与计算。直接缓存数组或对象身份也不可靠：历史修正可能原地更新，成交量可变化而收盘价不变，EMA 状态不能把同一根形成中的 K 线累计多次。
- **有效路径**：OHLC 和均线读数分别保存字段快照，保留节点并只写变化的文本。均线缓存核对每根历史的时间与收盘价，保存旧尾 K 之前的递推状态；尾 K 替换从该状态重算，历史修正、前插、裁剪及周期变化重新计算。缓存仅覆盖指标数值，OHLC、成交量和确认状态始终取当前输入。成交索引用二分查找后验证真实区间，缺口仍返回未找到。布局高度复用 ResizeObserver 的 borderBoxSize 并先读后写。
- **通用规则**：先测热路径中的 DOM 写入、重排及计算次数，再按最小必要输入建立缓存；与原全量计算比较尾 K 多次替换、旧数据修正、空序列、缺口、前插、裁剪和周期切换。性能测试不代替价格与时间语义验证。
- **牵连**：`yoyo/monitor/static/chart-style.js`、`range-chart.js`、`reclaim-chart.js`、`chart-drawings.js`、`monday-reclaim-paper.js`；原始浏览器对比和测试记录在 `output/playwright/chart-performance-20261029/`。Owner 本轮要求只优化性能、保持现有布局，未采用超时的 Stitch 新设计。borderBoxSize 语义参考 [MDN](https://developer.mozilla.org/en-US/docs/Web/API/ResizeObserverEntry/borderBoxSize)，图表接口参考 [Lightweight Charts 4.2](https://tradingview.github.io/lightweight-charts/docs/4.2/api/interfaces/IChartApi)。

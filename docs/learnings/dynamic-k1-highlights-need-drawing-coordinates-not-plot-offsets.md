# 动态间隔的 K1 高亮要用绘图坐标而不是 plot offset

- **问题**：K1 只有在后续 K2 和下一根开盘确认后才能确定，而且 K1→K2 间隔是 2–8 根；需要把已选中的历史 K1 蜡烛提亮并标出 K1/K2。
- **死胡同**：尝试给 `plotcandle` 或 `plotshape` 使用动态负 offset。`plotcandle` 没有 offset 参数，而 Pine v6 要求 plot offset 是运行期间不变的 simple 值；为每个 gap 复制整套 `plotcandle` 又会快速消耗 plot 数量。`barcolor` 只能改变主图原生蜡烛，无法覆盖本脚本自己的 `plotcandle`。
- **有效路径**：在因果 next-open 事件成立后，用动态 `bar_index` 和历史 OHLC 坐标创建两组 `line.new`：细线覆盖完整 high–low 影线，粗线覆盖 open–close 实体；再用 `label.new` 把 K1/K2 固定到准确的历史 K 线上。绘图对象支持 series 坐标，适合这种事后确认但不前视的标注。
- **通用规则**：需要高亮“动态距离的历史 bar”时，先区分 plot、主图 bar 和 drawing object 的所有权；Pine v6 优先用动态 drawing coordinates，不要把可变历史距离塞进 plot offset。
- **牵连**：`line.new`、`label.new`、`bar_index`、动态历史引用、`max_lines_count` / `max_labels_count`；官方依据为 TradingView 的 Text and shapes、Lines and boxes、Bar plotting 与 v6 migration 文档。

# 盈利队列筛选必须沿用收益解析器的状态和时间切分

- **问题**：3R扩样把发现、收益解析、队列选择、图片生成交给不同阶段，字段看起来相似但语义不同；队列层的初稿会误丢验证集赢家/输家，也可能重新收进已隔离的跨切点样本。
- **死胡同**：只写手工字典测试，使用假设的顶层`retained`或`TARGET/STOP`状态，再按核心结束时间重算split。真实解析器输出嵌套`profit`、`TP/SL/TIMEOUT`，split还考虑图片左端和完整12h标签窗口；字段拼写和只看核心时间都不能代替这些事实。初稿问题在正式运行前发现，没有据此训练。
- **有效路径**：直接调用真实收益解析器产生TP、SL、TIMEOUT测试数据，再通过队列选择验证后段三类都保留。选择层保留解析器的`train/val/test/purged`，只做预注册的参考邻域排除和训练容量选择。去重先于收益标签，最终ledger及selection receipt由SHA绑定，渲染器再次检查独立数量。
- **通用规则**：多阶段数据处理先用上一阶段的真实最小输出做契约测试；后续阶段不能为方便而重新推导已经判定的时间隔离。手写测试数据必须对真实输出作核对。
- **牵连**：`yoyo/contracts/ma_profit_filter.py`、`yoyo/datasets/ma_profit_pipeline.py`、`yoyo/datasets/ma_profit_cohort.py`、`tests/test_ma_profit_cohort.py`；实验`exp-ma-profit3r-20260922-v1`。

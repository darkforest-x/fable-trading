# 退出结论必须绑定页面实际展示的信号样本

- **问题**：市场共振页面默认严格条件 z3/v3/75%，却用宽松条件 z2/v2/60% 的 v5 退出研究解释“没有更好的退出”。同名策略并不代表相同样本。
- **死胡同**：只复述登记表的总结，会把研究范围省略掉；只比较各时长的平均收益，还可能因缺失路径不同而换了样本。
- **有效路径**：先查 config、summary 和 receipt，保留原结果并显示其条件；四个固定时长按相同完整事件及随机对照比较，分开前后时间段。默认严格条件另按已有退出规则复核，不自动改变策略。
- **通用规则**：展示结论前核对事件门槛、方向、品种、周期、完整路径与成本是否一致；范围不一致时明确标记，不能借用别组证据下结论。
- **牵连**：`yoyo/monitor/market_sync.py`、`yoyo/monitor/static/market-sync.js`、`experiments/active/exp-market-sync-shock-20261008-v5/config.json`、`experiments/active/exp-market-sync-shock-20261015-v6/config.json`。

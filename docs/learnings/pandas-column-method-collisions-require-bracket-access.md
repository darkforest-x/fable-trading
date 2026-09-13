# pandas 列名与方法重名时必须用方括号访问

- **问题**：市场广度面板确实生成了 `asof` 时间列，但 `panel.asof` 返回的是 DataFrame 的内置时间查询方法；最终时钟对齐因此把 method 传给 `DatetimeIndex` 并在长计算末尾失败。
- **死胡同**：把 DataFrame 的点号属性访问当成通用列访问。它只在列名不与 pandas 方法或属性冲突时碰巧有效，静态检查也不容易发现。
- **有效路径**：对契约字段统一使用 `frame["column"]`；点号形式只用于明确调用 DataFrame API。把完整面板时钟与预检时钟在写产物前逐元素比较，让列缺失、顺序漂移和时区漂移都 fail closed。
- **通用规则**：凡是 DataFrame 列来自外部 schema 或可能叫 `asof`、`mean`、`size`、`index` 等名称，第一步就用方括号访问；长任务的最终断言要用一个小型合成面板先执行。
- **牵连**：`yoyo/evaluation/spike_market_breadth_study.py` 的正式运行时钟检查；开发期 30m as-of 序列。

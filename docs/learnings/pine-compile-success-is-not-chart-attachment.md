# Pine 编译通过与成功叠加到图表是两个独立状态

- **问题**：TradingView 接受源码并完成编译后，仍可能因为当前套餐的指标数量上限而拒绝“添加到图表”；若只记录最终没出现指标，会把正确源码误报成编译失败。
- **死胡同**：把“添加到图表”按钮当成一个不可拆的成功/失败动作，或为了证明能显示就擅自删除 owner 当前布局里的指标。前者丢失编译证据，后者修改了用户云端布局且没有必要。
- **有效路径**：回执分别记录源码 SHA、official compiler run、compile error count、add-to-chart 状态、是否保存/发布、是否移除现有指标。出现套餐上限弹窗且编辑器无编译错误时，诚实报告“编译通过、叠加受配额阻塞”，不动原布局。
- **通用规则**：任何 Pine 浏览器 smoke 都把 compile、attach、save、publish、layout mutation 分成五个字段；验证源码只需要 compile，后四项必须按用户授权和产品限制分别处理。
- **牵连**：experiments/active/exp-two-key-candle-feature-atlas-v3/results/pine_compile_receipt.json；TradingView Basic 指标上限；参见 [browser-compiler-smoke-must-open-on-a-safe-market-window.md](browser-compiler-smoke-must-open-on-a-safe-market-window.md)。

# Pine 信号画在哪根，不决定交易何时知道它

- **问题**：ChartPrime 源码审查发现部分转向信号 offset=-1、枢轴回锚历史，以及未偏移 HTF OHLC 配 lookahead_on；历史图很容易被当成提前可交易的信号。
- **死胡同**：只看菱形所在K线，或只搜索“是否有barstate.isconfirmed”，都不能判断组成信号的每个输入是否当时已知；反过来看到图形重画就否定所有数值也过度。
- **有效路径**：分别追踪输入完成时间、计算事件时间、绘图锚点；把真正未来泄漏、枢轴确认延迟、负offset显示、可变对象和未收盘波动分开，逐条件追到实际使用路径。
- **通用规则**：移植前先给每个字段记录 available_at；只在当前可用输入上决策，不能按显示锚点回填成交。HTF泄漏只归于受影响的调用链，不牵连未使用该字段的本周期分支。
- **牵连**：exp-chartprime-public-confluence-audit-20260906-v1 的 AtJtdaDe/JqEFTgOE/8pOsueGg；https://www.tradingview.com/pine-script-docs/concepts/repainting/。

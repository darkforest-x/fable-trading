# Pine 历史错配要先比同根原始价，不能直接归咎数据源

- **问题**：V12 的 Python 参考识别 ONE 15m 的 A/B/C，TradingView 原生回放却因“历史实体穿线”拒绝。临时诊断在 2026-09-16 18:00 北京读到 `v10Body[offset]=0.0006518`，但同一 offset 的原始 open 约 0.0006463、close 0.0006420，明显不等于实体上沿；派生 ATR 也与逐根参考不符。
- **死胡同**：本地测试和 TV 编译通过不能证明原生信号正确；把派生 body 的值当成交易所 OHLC 差异也没有依据。全局变量的声明位置看似正确，不足以推翻实际嵌套、条件调用中的读值证据。不能先放宽实体容差来适配截图。
- **有效路径**：在同一个验证循环和 offset 同时打印 time、原始 open/close、派生 body、ATR、线价，再定位数据访问。V12 改为原始 OHLC 直接计算历史实体，ATR 在每根 bar 无条件写入有限环形数组，按绝对 bar 索引读取；主周期与 request.security 上级周期各自持有上下文。原生回放验收另记在本轮报告，不把源码审查当执行证明。
- **通用规则**：参考引擎与原生平台不一致时，先比较同一决策时刻、同一历史 bar 的原始值与派生值，再比较过滤阈值。区分已观察到的读值错配与尚未证明的编译器内部机制。
- **牵连**：`yoyo/evaluation/pine/spike_burst_v12.pine`；只修 V12，V11.2 保留原文件。历史公式和容差不改；Pine 官方 [Time series in scopes](https://www.tradingview.com/pine-script-docs/language/execution-model/#time-series-in-scopes)、[请求上下文与数组](https://www.tradingview.com/pine-script-docs/writing/profiling-and-optimization/)。

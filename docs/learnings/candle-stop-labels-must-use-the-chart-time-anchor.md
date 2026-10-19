# 止损来源必须按图表开盘时间定位，并展示完整 K 线时段

- **问题**：Owner 怀疑 5m 高低点选错；原页面只写收盘时刻，而图表横轴用开盘时刻，且没有标明实际取值的 K 线。
- **死胡同**：仅因为止损很近或仓位过大就判定高低点算法错，或把“5m 周期高低点”自动改成多根 K 线波段极值。前者缺证据，后者改变已确认的风险规则。
- **有效路径**：按保存的来源开盘时间直接读取交易所历史原始数组，与该次参考和图表逐值比较。PARTI 2026-10-17 15:00–15:05 北京时间 high=0.03345、low=0.03330，两端完全一致。前端改为完整开闭区间；箭头以 open_ms 绑定真实 bar，并核对 high/low，缺失或不一致给出明确提示。将选项说明为上一根5m K线，等待 Owner 澄清单根与波段规则。
- **通用规则**：先核对来源时段、闭合状态与字段，再判定计算错误；“单根高低点”与“周期上的波段高低点”须分开确认。图表标注必须采用与横轴一致的时间锚点。
- **牵连**：manual-position-chart.js、manual-live.js、frontend_manual_chart.test.cjs。OKX `/api/v5/market/candles` 的 ts 是开盘毫秒、数组 [ts,o,h,l,c,vol,volCcy,volCcyQuote,confirm]，confirm=1 已闭合；文档 https://www.okx.com/docs-v5/en/#rest-api-market-data-get-candlesticks 。原始对照 output/manual-ui-20261017/stop-candle-audit.json。本轮不改变止损算法、仓位或执行参数。

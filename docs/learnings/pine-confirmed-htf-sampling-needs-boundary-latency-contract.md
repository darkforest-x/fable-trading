# Pine已确认高周期取样需要单列边界延迟

- **问题**：把Python多周期研究写成收盘确认的Pine指标时，需要同时保证实时/历史时钟一致，不能默认为逐笔信号已与研究回放相同。
- **死胡同**：仅用lookahead_off读取当根高周期，会让实时未收盘值参与历史上不存在的判断；把barstate.isconfirmed放进request.security也不能解决。直接把研究收益贴给移植后的脚本同样缺证据。
- **有效路径**：高周期md、sh、time_close和bar_index都在请求上下文中取[1]，再配lookahead_on。图表状态只在本周期收盘更新；低周期使用security_lower_tf数组，缺失不放行。这样大周期刚与本周期同时收盘的状态要到下一根图表bar才参与收盘判断，比Python的source_close<=decision_close口径晚一根图表K线。
- **通用规则**：无重绘设计和研究逐笔一致是两项验证。先写清更新时间与允许延迟，再校验源代码、编译和实际运行；不要用安全取样口号替代时钟说明。
- **牵连**：`yoyo/evaluation/pine/imacd_dense_mtf_v1.pine`；`yoyo/evaluation/imacd_ma_mtf.py`的align_closed；[TradingView官方多周期文档](https://www.tradingview.com/pine-script-docs/concepts/other-timeframes-and-data/)。

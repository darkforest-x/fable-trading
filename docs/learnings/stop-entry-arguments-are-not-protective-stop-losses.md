# 先辨认订单函数，再解释参数里的 stop

- **问题**：ChartArt BB＋RSI 的高胜率曲线被用于讨论止损后倍投；源码里确实出现stop参数，但账户风险是否固定尚不清楚。
- **死胡同**：只读策略介绍“超买超卖”或把所有stop参数都当保护止损，会误造每单1R和连续止损统计。仅凭截图胜率也无法判断每次亏损金额。
- **有效路径**：从作者官方页面内嵌源码确认stop属于strategy.entry；双重cross成立时stop已经被当前价越过，默认引擎下一tick执行。文件没有退出函数，离场实际来自反向entry。随后先重建原始单位交易，再在同一交易路径上改变账户仓位。
- **通用规则**：参数名称必须连同调用函数、订单创建时点和当前价格一起解释。无初始止损的策略不能凭空报告1R或连续止损；亏损后翻倍要使用真实净亏损，不可由胜率推出回本。
- **牵连**：yoyo/evaluation/chartart_bbrsi.py；exp-chartart-bbrsi-martingale-20260915-v1；[作者脚本](https://www.tradingview.com/script/uCV8I4xA-Bollinger-RSI-Double-Strategy-by-ChartArt-v1-1/)与[Pine订单规则](https://www.tradingview.com/pine-script-docs/concepts/strategies/)。

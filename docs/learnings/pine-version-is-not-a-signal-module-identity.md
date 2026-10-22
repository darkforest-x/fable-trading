# Pine 版本号不能替代信号模块身份

- **问题**：Owner 指定 V2.37 的“触价高亮”要求回测，但同版本还含另一套多周期交易框，注册表中的 V2.37 两年实验研究的是后者。
- **死胡同**：只按版本号检索会首先找到 5m/15m 配对、5R 启动跟踪的结果；这些账本不含触价高亮要求的同关键位实体避让、影线穿透及周一区间中点过滤。不能把它们作为本次模块的收益证据。
- **有效路径**：从用户可见开关 `wfWicksEnabled` 定位 `WfTrade` / `wfQuarter`；直接比较 V2.31 与 V2.37 从 level-touch engine 起的源码，整个区块完全一致。再核对配置默认值及已登记五年研究，复用 V2.31 的 485 BTC / 385 ETH 原生逐笔。图上回标局部极值与确认收盘成交继续分别解释。
- **通用规则**：先锁定可见功能对应的引擎、输入与退出语义，再沿实现和登记定位账本。同一指标版本可以包含多个独立策略；旧版本命名的实验也可能恰好是新版本未改模块的权威结果。
- **牵连**：`yoyo/evaluation/pine/monday_range_v2_31.pine`、`monday_range_v2_37.pine`、`experiments/active/exp-range-v231-fiveyear-20261021-v1/config.json`、`yoyo/evaluation/range_touch_multidim.py`。不得混用 V2.37 多周期框两年结果。

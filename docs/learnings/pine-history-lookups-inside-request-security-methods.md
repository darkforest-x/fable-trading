# request.security 里由方法做的 `[]` 历史取值不可信，引擎要自带历史数组

- **问题**　SPIKE 云带 V1.8 把 4H 主线引擎放进 `request.security(…, "240", trendEngine())`，用来跨周期显示同一条 4H 线。引擎的 `clean()` 是方法，里面用 `high[k]`、`tlAtr[k]` 回看。2026-11-10 在 OKX:LPTUSDT.P 上，引擎接受了一条线，可 2025-07-18 12:00 的影线 7.811 远高于线位 7.20；同时各条线的贴合度数值也和 Python 对不上。
- **死胡同**　V1.8 在 PIEVERSE 上和 Python 逐条一致（8 条线、8 次上破），就当成了已对齐。到 V1.9 出现分歧时，先以为是 TradingView 在新币上线初期的 K 线和 OKX REST 数据不同，还为此查了缺口和插针。只在一个币上对齐，不能说明引擎没问题。
- **有效路径**　加一个只在 4H 图、只开调试时运行的图表端复核：用图表自身的序列，对每条新线重算穿线根数和贴合度，再和引擎导出的贴合度并排打印。LPT 第一条线图表端是 49 根穿线、贴合度 0.324，与 Python 完全一致；引擎给出的贴合度却是 2.607，且判定无穿线，问题就锁定在 security 内部。修法：引擎在每根已确认 4H 收盘时，把带符号的影线、实体、ATR、开盘时间写进自己的数组，所有回看都按 bar index 读数组，不再在方法里用 `[]`。修复后 LPT 9/9、PIEVERSE 3/3 与 Python 一致，每条线的引擎贴合度都等于图表端复核值。
- **通用规则**　在 `request.security` 调用的函数或方法里，不要依赖 `[]` 动态偏移读历史，包括全局序列和内置的 `high`、`time`；把需要的历史存进引擎自己的 `var` 数组。验收时要有一个独立于引擎的图表端重算对照，并至少换两个币核对，其中一个要选历史长、线多的币。
- **牵连**　`yoyo/evaluation/pine/spike_signal_cloud_v1_8.pine` 有此缺陷，可能画出被 K 线穿过的线。`spike_signal_cloud_v1_9.pine` 已改用 `Bars` 数组。原版 `auto_trendline_4h_v1_2.pine` 直接在图表上运行，不受影响。另见 [tradingview-pivothigh-allows-left-ties.md](tradingview-pivothigh-allows-left-ties.md)。

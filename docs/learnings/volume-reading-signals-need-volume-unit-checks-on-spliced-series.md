# 读成交量的信号，拼接行情前要逐根核对量的单位

- **问题**：Owner 要回测 ETHUSDT.P 5m 的 SPIKE V12.9。他在 TradingView 上看的是 OKX，但本地最长的现成序列是币安 5 年数据；现成的 OKX 5m 序列（`htf_ma_reversion_okx_eth_5m_20261009`）尾段则是另外拉的 5m 接口数据。
- **死胡同**：(1) 直接用币安序列：交易所不同，成交量结构和插针都不一样，结果不能代表 owner 看的图。(2) 直接用现成的 OKX 5m 序列：它的拼接回执只比对了 OHLC，没有比对成交量，builder 还写着「volume 只为格式对齐，策略不读」。但 SPIKE 的准入要读放量（rv≤50、3 根放量比），量的单位一变，拼接点前后的信号会被悄悄改掉，K 线看上去却完全正常。
- **有效路径**：先拉一页落在归档区间内的 OKX history-candles 1m，逐根比对 `r[5]`（张）和归档 volume：100/100 根比值都是 1.000，收盘价差为 0。确认后改用「官方 1m 归档 + 已登记的 1m 接口 + 新拉的 1m 接口」，统一聚合成完整的 5m/15m 桶，再把 2026-09-22 那次 OKX 低周期回放的 241 笔联合交易逐笔复现，作为整条链路的验收。
- **通用规则**：只要策略读成交量，拼接不同来源时必须做一次重叠区间的逐根成交量比对（不能只比 OHLC），并在回执里写明单位（张 / 币 / 计价币）。品种名里的 `.P` 不带交易所信息，先查 HANDOFF 或 owner 样例里用的是哪家。
- **牵连**：`yoyo/evaluation/spike_v129_eth5m.py`、`spike_lowtf_v1_v126.fetch_recent`（取 `r[5]`）、`experiments/active/exp-htf-ma-reversion-20261009-v3/build_series.py`（只比对 OHLC，策略不读量，所以在那个实验里没问题）。相关：[cross-venue-confirmation-is-event-evidence-before-it-is-entry-alpha.md](cross-venue-confirmation-is-event-evidence-before-it-is-entry-alpha.md)

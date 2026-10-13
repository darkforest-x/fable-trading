# 数据商声明的时区要按夏令时季节用定时事件指纹验证

- **问题**：复刻 TradingView「Gold Breakout 15M」（GMT+3 00:00–07:00 亚盘区间突破）时，用 HistData.com
  XAUUSD M1。官网写明时间戳是「EST，不含夏令时」，于是按固定 UTC-5 转换。
- **死胡同**：直接信文档。固定 +5h 下冬季完全正确，夏季每根 K 线晚 1 小时——区间窗口、开仓窗口、
  20:00 平仓全部错位，但回测照样跑出一套「看起来合理」的数字，不会报错。只看全年平均的价格相关性也看不出来，
  因为冬季那半年是对的。
- **有效路径**：用固定时刻发生的事件做指纹，按季节分开看。
  ① NFP（纽约 08:30）的 15m 波动尖峰：+5h 后冬夏都落在 13:30，而真实 UTC 夏季应是 12:30；
  ② 每日 17:00（纽约本地）休市缺口，冬夏都落在同一个 +5h 小时。两者都说明时间戳是**纽约本地挂钟时间（含 DST）**。
  ③ 再拿真 UTC 数据源（OKX XAU-USDT-SWAP 15m，2025 夏季）做收益相关：固定 UTC-5 为 0.12，
  `tz_localize("America/New_York")` 为 0.86；冬季两者都是 0.97。
- **通用规则**：凡是依赖时段（亚盘区间、开盘、日内平仓）的策略，换数据源第一步就是按冬/夏分别找
  一个定时事件（NFP、每日休市、现货开盘）确认它落在预期 UTC 时刻；能找到真 UTC 源就再做分季节的收益相关。
  不要用全年平均——DST 错误只污染半年。
- **牵连**：`experiments/active/exp-gold-breakout-15m-willy-20261013-v1/backtest.py::load_15m`。
  HistData 2026 月度包里另有 696 个价格冲突的重复分钟（中位差 0.9bp），整分钟剔除与保留首条结果逐位相同；
  见 [vendor-archive-duplicates-must-be-proven-identical-before-collapse](vendor-archive-duplicates-must-be-proven-identical-before-collapse.md)。
  [gold-session-bars-and-shared-drawdown-grid](gold-session-bars-and-shared-drawdown-grid.md) 里「固定 EST」的说法
  对 XAUUSD 时间戳不成立。

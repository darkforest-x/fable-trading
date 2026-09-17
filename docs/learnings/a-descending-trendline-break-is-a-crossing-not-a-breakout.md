# 下降趋势线的"突破"是穿越，不是上涨——线会自己降下来找价格

- **问题**：2026-09-18 移植 owner 的「主下降趋势线 · 关键高点 V1」时，写了一个
  "价格全程横盘就不该有突破"的测试，结果**测试失败了**：价格从头到尾是 100，
  照样出一个突破信号。

- **死胡同**：第一反应是移植错了——大概率是 `f_isClean` 的窗口写反，或者突破判定
  读了未来 bar。逐行比对 Pine 之后，移植是对的，**原指标本来就是这个行为**。
  差点做出的错误修复：给移植加一条"价格必须高于建线时的收盘"的条件，让测试变绿。
  那不是修 bug，那是**偷偷改 owner 的指标**，而且会让回测与 owner 在 TradingView 上
  看到的图对不上。

- **有效路径**：信号条件是 `close > 线的当前值 + 缓冲`，而线是**向下倾斜**的。
  线的斜率由两个锚点决定，横盘期间线每根 K 都往下走一点；只要线够陡、寿命够长
  （`lookback` 600 根），它迟早会降到横盘价格上，于是在完全没有上涨的行情里触发。
  斜率足够缓时线会先到期退出监测，触发不了——这两种情况用两个测试分别钉住：
  一个证明"到期不算突破"，一个证明"线降到价格上算突破"。

  结论不是"指标坏了"，而是**统计口径要改名**：原始 break 数应该读成"穿越次数"，
  不是"突破次数"。这直接改变了报告怎么解释信号量：15m 上 54 个币四年 4 万多个信号，
  其中相当一部分是线自己走下来碰到的，不是行情走上去撞破的。

- **通用规则**：**一条随时间移动的阈值线，它的"被突破"包含两种完全不同的事件——
  价格上去了，和线下来了。** 报任何跨越型信号之前，先构造一个"被跨越的一侧完全不动"
  的合成序列跑一遍：如果还出信号，那这个信号名里的动词是错的，统计口径和后续的
  经济学解释都要跟着改。
  想给"线下来了"这一类加过滤是合理的研究方向，但那是**改入场**，属于新实验、
  新的单变量，不能塞进一个只搜出场的实验里（CLAUDE.md 铁律 4）。

- **牵连**：
  - `yoyo/evaluation/trendline_v2_signals.py::detect`
  - `tests/evaluation/test_trendline_v2.py::test_a_steep_line_descending_into_flat_price_still_fires_a_break`
  - `tests/evaluation/test_trendline_v2.py::test_expiry_retires_a_line_without_reporting_a_break`
  - owner 原文逐字存档：`yoyo/evaluation/pine/trendline_key_high_v1_owner.pine`
  - 相关：[net-r-grid-search-drifts-to-wide-stops-because-fees-shrink-in-r](net-r-grid-search-drifts-to-wide-stops-because-fees-shrink-in-r.md)

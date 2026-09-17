# 截断冻结池后做逐笔对照，边界要取最后一根保留 K 线的**开盘**

- **问题**：2026-09-18 给 SPIKE V10 做回测时，为了 holdout 消耗为 0，把 3,531 条冻结流
  全部截断在 2026-05-04 之前（开盘 ≥ 该时刻的 K 线一根不读）。为了证明「截断没有改变
  截断点之前的任何决策」，把 `v9` 臂与已发布的 V9 账本逐笔对照：凡是在截断点之前平仓的
  交易必须完全一致。写成 `exit_time <= 最后一根保留K线的收盘`，结果 2 条流报「笔数不一致」。

- **死胡同**：第一反应是截断破坏了串行引擎的状态——大概率是缓存里某个 aligned 序列没跟着切，
  或者 ATR 预热被截断影响了。差点做的错误修复是给对照加一条容差（「允许末尾差一笔」），
  那会让这道检查永久失效，而它是整个 holdout 纪律唯一的物证。

  还有一个更糟的方向：把截断点往后挪一根让两边对齐。那等于为了让测试变绿去读 holdout。

- **有效路径**：打印两边账本才看清。已发布账本里那笔的 `exit_time` 正好是
  `2026-05-04 00:00:00Z`，也就是**切点本身**。原因是出场时间戳记的是**出场那根的开盘**
  （`exit_time_precision = bar_open_or_intrabar_window`），所以「时间戳等于切点」意味着
  这笔是在 **holdout 的第一根**上出场的。它属于被切掉的尾部，本次回放里同一个仓位只是
  还没平、被标成 censored。

  把边界从「最后一根保留 K 线的收盘」改成「最后一根保留 K 线的**开盘**」，两边当场一致。
  **检查是对的，边界差了一根。**

- **通用规则**：**切点两侧做任何逐笔对照之前，先问「这个时间戳是这根的开盘还是收盘」。**
  K 线数据里同一个瞬间同时是前一根的收盘和后一根的开盘；只要账本用开盘打时间戳（次开盘成交、
  盘中止损窗口都是这种），那么「时间戳 == 切点」的事件用的是切点之后那根的数据。
  对照窗口的右端要用**保留数据里最后一根的开盘**，不是它的收盘，也不是切点本身。

  推论：这类对照失败时，先把两边的行打出来看一眼，再决定是不是引擎坏了。这次如果按
  第一直觉去「修引擎」，改动的是一个本来正确的截断实现。

- **牵连**：
  - `yoyo/evaluation/spike_v10_full_replay.py::compare_v9_ledger` / `v9_parity`（边界 = `bars.index[-1]`）
  - `tests/evaluation/test_spike_v10_full_replay.py::test_parity_excludes_a_published_exit_that_landed_on_the_first_dropped_bar`
  - `yoyo/contracts/holdout.py::HOLDOUT_START`（2026-05-04T00:00:00Z，含）
  - 同一次对照还抓到另一个问题：用 `Series.equals` 比较列会连 dtype 一起比，
    两边都为空时 CSV 读出的空列与回放构造的空列 dtype 不同，22 条流因此误报。
    见 `tests/evaluation/test_spike_v10_full_replay.py::test_parity_accepts_a_stream_whose_every_trade_opened_after_the_cut`
  - 相关：[a-descending-trendline-break-is-a-crossing-not-a-breakout](a-descending-trendline-break-is-a-crossing-not-a-breakout.md)

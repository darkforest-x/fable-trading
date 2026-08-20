# 对累计 streak 做 rolling max ≠ 窗口内最长连续段

- **问题**：yoyo-eth MVP 的特征 `max_consecutive_close_above_10`（最近 10 根内
  close>均线簇中心的最长连续段）。直觉实现：先算"截至每根 bar 的连续计数"
  `streak[t]`，再 `streak.rolling(10).max()`。
- **死胡同**：这个实现通过了 Future Mutation Test（它确实只看过去，无前视），
  通过了全部单元测试，管道跑通、数字"合理"——所以自测根本发现不了。问题是
  `streak[j]` 的计数可以延伸到 10 根窗口**开始之前**：一段 20 根的连续段在窗口
  内只剩 6 根时，特征值仍是 10（clip 上限）而不是 6。特征名义语义（within the
  last 10 bars）与实际语义（ending within the last 10 bars, unbounded lookback）
  静默偏离。方向没错、无泄漏,但特征的"记忆长度"不受声明窗口约束。
- **有效路径**：对抗性审查专门核对"每个特征的名义窗口 vs 实际回看"时用最小
  复现抓到。修法：窗口内位置 j（0 起）处的真实段长 = `min(streak[j], j+1)`，
  再取 max——`rolling(10).apply(lambda a: np.minimum(a, np.arange(1,11)).max())`。
  修复后特征分布改变，模型必须重训。
- **通用规则**：任何"窗口内的 XX 统计"特征，如果基于一个跨窗口累计量（streak、
  cumsum、从任意起点起算的 duration）二次聚合，先问一句：**累计量的起点会不会
  跑到窗口外？** 会，就必须在窗口内重新截断（min(累计值, 窗口内位置+1)）。
  同类风险：`compression_duration` 这类无界 streak 特征——它们让任何有限 embargo
  gap 都无法完全隔断 split 信息，只能在报告里声明。
- **牵连**：`/Users/zhangzc/yoyo-eth/src/yoyo_eth/features.py`（`_max_in_window`）、
  `tests/test_mvp.py::test_max_consecutive_close_above_window_capped`；
  相关：无前视 ≠ 语义正确——Future Mutation Test 只护住因果性，护不住窗口语义。

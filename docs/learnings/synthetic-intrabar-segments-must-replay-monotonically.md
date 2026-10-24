# 合成的K线内路径段必须单调回放，否则换顺序等于没换

- **问题**：为了核查 Codex 1.618 挂单收回里做空同K止盈是否真实，我用框架把同一批信号按 O-L-H-C 重跑。结果 15,022 笔做空没有一笔变化，807 笔同K止盈全部保留。可是这些成交K线里只有 12.8% 的收盘价在止盈价之下，按真正的"先低后高"走法，大多数本该算不上止盈。
- **死胡同**：第一反应是"这批做空同K止盈是真的"，差点就当成结论告诉 owner。这次"零变化"本身就是异常信号：多头有 485 笔变化，空头却一笔都没有，两边不对称。
- **有效路径**：用一根K线的最小例子复现。做空挂 101、止盈 99，K线 O100/H102/L98/C101，按 O-L-H-C 仍然算出止盈 99。原因在 `ohlc_path_frame`：它把一根K线拆成几段单调的合成小K线，而引擎在 `bar_adaptive_high_low_ordering=False` 时，每段都按"开→高→低→收"回放。上涨段（开=低、收=高）于是走成"低→高→低→高"，在高点成交之后又回到低点，把止盈触发了。改为可选开关 `monotone_segments=True`（启用自适应顺序，对单调段是精确的）后，最小例子按 O-L-H-C 不止盈、按 O-H-L-C 止盈，结果正确。默认行为不变，297 个相关测试全部通过。v1 输出作废，v2 用新开关重跑；另抽 20 个币，用 O-H-L-C 加新开关重跑，核对 Codex 原账本是否受影响。
- **通用规则**：换K线内顺序做敏感性检查时，先用一根K线的最小例子确认两种顺序真的给出不同结果。只有一侧变化、另一侧完全不变时，先怀疑回放机制，再去解读结论。
- **牵连**：`yoyo/layers/l3_backtest/nautilus_independent.py`（`ohlc_path_frame`、`ExitPolicy.monotone_segments`、`add_venue` 的 `bar_adaptive_high_low_ordering`）；`tests/evaluation/test_nautilus_monotone_segments.py`；`yoyo/evaluation/monday_limit_reclaim_olhc.py`。相关笔记：[same-bar-target-fills-under-ohlc-path-inflate-fast-spike-shorts.md](same-bar-target-fills-under-ohlc-path-inflate-fast-spike-shorts.md)、[exit-bar-mfe-needs-intrabar-bounds.md](exit-bar-mfe-needs-intrabar-bounds.md)。

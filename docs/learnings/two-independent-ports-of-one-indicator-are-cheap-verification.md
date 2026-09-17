# 同一份指标的两份独立移植互校，是最便宜的一次验证

- **问题**：2026-09-18 owner 同一天提了两个需求：把「主下降趋势线 · 关键高点 V1」做成
  独立策略（搜 TP/SL），以及把它的突破合进 SPIKE V9 做成 V10 的入场门。两个会话各自
  移植了同一份 Pine，于是工作树里出现了 `trendline_v2_signals.py` 和 `trendline_break.py`
  两份实现。第一反应是重复代码要合并。

- **死胡同**：直接合并会踩两个坑。一是那份文件当时正在被另一个会话改，
  **builder 在实验跑的过程中变过，任何复现声明都作废**
  （见 [artifacts-built-before-their-builder-landed](artifacts-built-before-their-builder-landed.md)）。
  二是两份的适用边界本来就不同：一份只做多、假设输入无缺口；另一份要处理数据缺口、
  还要空头镜像。硬合并会把一方的假设偷偷带给另一方。

- **有效路径**：不合并，改成**互相钉住**。写一个测试让两份实现跑同一组输入
  （6 个种子 × 6000 根），断言 `break_event` / `born_event` / `line_active` 逐根一致，
  并在两份的 docstring 里互相点名说明为什么共存。

  这次互校的价值远超合并：两份是**从同一份 Pine 独立写出来的**，
  一致意味着「两个人分别读同一段代码得出同一个语义」。单份实现无论加多少测试，
  都测不出「我把 Pine 读错了」这一类错误——测试和实现出自同一个理解。
  本仓已经有一次同类代价：两个 ATR 实现在 bar 14 差 0.109，没人发现，因为没有对照
  （`docs/consolidation/DUPLICATE_SEMANTICS.md` §4）。

- **通用规则**：**重复实现不一定要消除，但一定要有一条测试让它们不能悄悄分叉。**
  当重复来自「同一份外部规格的两次独立翻译」时，它是资产不是债务——先写互校测试，
  再决定合不合。反过来，如果两份的差异没有测试覆盖，那它就是下一个 ATR 分歧。

  判断标准：合并的前提是两份的**假设集合**相同。假设不同（缺口处理、方向、输入契约）
  就保留两份 + 互校，并在各自 docstring 写明边界。

- **牵连**：
  - `yoyo/evaluation/trendline_break.py`（V9 门用，含缺口重置与空头镜像）
  - `yoyo/evaluation/trendline_v2_signals.py`（独立策略用，只做多、假设无缺口）
  - `tests/evaluation/test_trendline_break.py::test_agrees_with_the_independent_sibling_port`
  - `tests/evaluation/test_trendline_break.py::test_matches_literal_pine_transcription`
    （第三条对照：把 Pine 的嵌套循环逐字照抄的参照实现）
  - `docs/consolidation/DUPLICATE_SEMANTICS.md` §4（没有对照的重复实现的代价）

# 冻结研究引擎里藏着硬日期窗，直接拿来跑实时会静默清零

- **问题**：给 V9 实时信号卡片接持仓跟踪。最自然的做法是复用全量回测的入口
  `yoyo/evaluation/spike_v9.py::replay_v9`——同一个引擎，卡片上的 R 就天然等于回测的 R。
- **死胡同**：`replay_v9` → `replay_mask` → `spike_exit_policy_study.replay_policy` →
  `_cohort_inputs`，最后一层有 `in_window = (close >= START) & (close < END)`，
  `START=2024-09-10 / END=2026-09-10` 是模块级常量，窗外的 `long_signal/short_signal`
  **被就地置 False**。今天是 2026-09-17，整条实时管道会一条信号都不剩，而且不报错、不告警，
  就是空结果。三层调用之间没有任何一处提到这个窗口，函数名和 docstring 也不提。
- **有效路径**：改用同族的 `spike_v6_wvf_study.simulate_v6_variant`（接收 signals + admission，
  没有日期常量），再把「两个实现必须一致」钉进 `tests/parity/test_duplicate_semantics.py`：
  同一批 K 线上两引擎逐笔对照，ETH 1H 真实数据 14 笔全部相同、R 差 ≤3.6e-15，唯一差异是
  未平仓那笔（冻结引擎给 NaN 拒绝估值，卡片必须给 mark）——这条差异也写进断言，不让它悄悄漂移。
- **通用规则**：把研究代码接进实时路径前，先搜整条调用链里的**模块级日期/边界常量**
  （`START` / `END` / `SPLIT` / `HOLDOUT_*`），而不是只看最外层函数签名。研究引擎默认活在
  一个冻结窗口里，那是它的正确行为；实时调用者才是异类。判据很简单：**拿今天的时间戳跑一遍，
  看输出是不是空的**——空结果比异常更危险，因为它长得像「今天没信号」。
  复用成立与否，靠逐笔 parity 测试证明，不靠「都叫同一个名字」。
- **牵连**：`yoyo/monitor/v9_performance.py`（新）、`yoyo/monitor/v9_signals.py`、
  `yoyo/evaluation/spike_exit_policy_study.py:30-32`（常量）、
  `tests/parity/test_duplicate_semantics.py`。同类问题见
  [duplicate-semantics 清单](../consolidation/DUPLICATE_SEMANTICS.md)。

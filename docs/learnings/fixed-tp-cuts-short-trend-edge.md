# 固定障碍在空边砍趋势；无 TP / 跟踪才过 1.3

- **问题**：入场已找到相对最强的 spread-short（TP5/SL2 下 PF 1.245 仍 <1.3），是否
  该继续扫入场，还是承认策略是趋势、改出场？
- **死胡同**：在固定 TP5 下继续换择向 / 启动规则——空边顶在 ~1.25，多边全面 <1.0；
  把「过 1.3」押在入场网格上会空转。时间止损能把净合计抬很高，但 PF 仍 <1.3（厚赚厚亏）。
- **有效路径**：owner 批趋势出场后，**固定同一入场**（spread_expand_chg8），只换出场。
  空边 `no_tp_sl2` / ATR trail3 / EMA55 退出把 train PF@maker 抬到 **1.415 / 1.339 / 1.316**。
  关键判断：固定 TP 在有偏置的空边上是在截断赢家。
- **通用规则**：一边 base rate 已接近成功线时，下一刀优先动**与策略叙事一致的那一端**
  （趋势 → 出场；均值回归 → 障碍宽度），不要无归因地同时扫入场×出场网格。
- **牵连**：`scripts/trend_exit_base_rate.py`；`analysis/p_trend_exit_base_rate.md`；
  `label_candidate_sl_only` / short trailing·MA / structure；对照
  `p_launch_entry_long_short.md`。过线仍属 train，非 holdout/前向。

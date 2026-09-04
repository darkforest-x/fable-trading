# 盘中 MFE 触线不等于收盘已激活 runner

- **问题**：失败归因曾把 `mfe_at_exit_atr >= 2` 的亏损都叫作“已激活后回吐”，但策略的真实激活条件是完成 K 线收盘达到 `+2ATR`。
- **死胡同**：用 OHLC 的最高/最低价代替状态机事件。盘中影线可以越过 +2ATR 后收回，导致 MFE 达标而 `runner_armed=false`；本轮 77 个旧标签里只有 53 个真实激活。继续沿用会夸大退出问题、低估入场前失败。
- **有效路径**：分类一律以回测账本的 `runner_armed` 为主；MFE 只描述到过哪里。退出回吐使用 `mfe_at_exit_atr - realized_atr`，完整 horizon 的最大行情只记为 `horizon_opportunity_gap_atr`，不能混作退出前已实现机会。
- **通用规则**：任何路径诊断都必须从策略状态字段恢复事件，不得由价格极值反推状态；同时把“截至实际退出”和“退出后直到标签 horizon”分开命名。
- **牵连**：`audit_selected_trades.csv.gz`、`corrected_failure_mechanics.csv.gz`；runner 激活 `close >= +2ATR`，而非 intrabar high/low；15m OHLC 仍无法恢复 bar 内先后顺序。

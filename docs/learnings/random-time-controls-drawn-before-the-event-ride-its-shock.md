# Random-time controls drawn before the event ride the event's own shock

- **问题**：触价收盘幸存切片（评分 ≥0.5，收盘仍在 1.618 外）要和同币、同周、同方向、同波动桶的随机时间进场对照（`exp-monday-limit-close-slice-control-20261101-v1`）。每个事件配 3 个随机 5m 收盘，按原事件等比例搬到一条虚拟 1.618 外进场，出场规则不变。
- **死胡同**：按预设结果，信号每笔 +143bp，随机 −66bp，差 +197bp，看起来很干净。但三组随机对照差别很大：c0 −138bp、c1 −80bp、c2 −6bp。随机组是按时间编号的，c0 多数落在真实触价之前。落在真实触价之前的 269 笔平均 −280bp：它们在虚拟位进场后，接着吃到了真实那次插针的反向冲击，被 −30% 止损或未收回平仓。这部分差距来自“对照在事件前进场”，不全是信号的功劳。
- **有效路径**：拆成触价前和触价后两组随机。只拿触价后的随机进场（−11bp）做对照，2025–26 年差距仍有 +139bp（95% 区间 +44～+229），2026 年 +173bp；2025 年单年 +102bp，区间跨 0。结论仍成立，但效应从 +197 缩到 +139，2025 单年不显著。
- **通用规则**：同窗口随机时间对照里，凡是落在真实事件之前的对照，会把事件本身的冲击算进对照的亏损。报告时按“对照在事件前/后”拆开，以事件后的对照作为严格版；或者在预设里就只从事件之后抽样。
- **牵连**：`yoyo/evaluation/monday_limit_close_slice_control.py`（随机行来自 `exp-monday-limit-close-reclaim-20261028-v1` 的 pair_id）；拆分是读完结果后补做的。相关：[touch-bar-information-pays-only-before-the-price-recovers.md](touch-bar-information-pays-only-before-the-price-recovers.md)、[reclaim-controls-must-match-distance-from-the-level.md](reclaim-controls-must-match-distance-from-the-level.md)。

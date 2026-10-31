# An alarm threshold's per-trade quality and its account result peak in different places

- **问题**：25 币报警进场能用，Owner 问“这个25是最好的参数？”，要求先测门槛稳健性（`exp-monday-limit-alarm-threshold-20261101-v1`）。规则其余部分不变，把门槛换成 10/15/20/25/35/50/75。
- **死胡同**：直接按账户倍数挑：20 币全期 4.72x，高于 25 币的 3.70x，看起来应该换 20。但单年看，25 的 2025 年每笔 +3bp，旁边的 20 是 −19bp、35 是 −66bp，25 的 2025 结果只是刚好落在正的一侧。挑账户最高的门槛，就是在同一段历史上挑噪声。
- **有效路径**：把“每次报警行情的质量”和“账户倍数”分开看。三种出场规则下，每次报警的平均收益都随门槛单调上升（near30：+48、+82、+146、+162、+184、+244、+309bp）。所以报警这个信号本身是一条剂量-反应曲线，25 不是刀尖上的参数。账户倍数峰值落在 20–25，是因为笔数和质量相乘：门槛降到 10–15，2025 年每笔 −58～−66bp，20 份 3 倍账户在 2025 年爆掉；门槛升到 50–75，质量更高，但一年只有几次，复利起不来。
- **通用规则**：被问“这个参数是不是最好”时，先画每单位独立事件的质量随参数变化的曲线，确认单调、平滑，再看账户结果。账户峰值反映的是笔数×质量的折中，不是信号最强的位置；相邻值的单年结果正负跳动时，不要从中挑一个当新参数。
- **牵连**：`yoyo/evaluation/monday_limit_alarm_threshold.py`、`exp-monday-limit-alarm-entry-20261029-v1`、`exp-monday-limit-alarm-combo-20261031-v1` 的成交复用；所有门槛都在同一段历史上看过。相关：[a-score-that-only-earns-in-crash-bars-dilutes-the-alarm-leg.md](a-score-that-only-earns-in-crash-bars-dilutes-the-alarm-leg.md)。

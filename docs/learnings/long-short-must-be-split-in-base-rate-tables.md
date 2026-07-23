# 多空混池 PF 不是「方向规则错了」——是测量没分边

- **问题**：启动入场 base rate 把跟向多空合成一行 PF；owner 一眼看出「多空没区分好」，怀疑方向写反。
- **死胡同**：先假设突破向上却开空 / close 相对中轴符号反了，去改入场规则——但 `direction_audit` 显示 range/vol mismatch=0，规则本身跟对了。把「报告难看」当成「信号定义 bug」会浪费一轮改定义。
- **有效路径**：强制每变体输出 long-only | short-only | both；both 降为对照。分边后真相是多边全薄（≤0.94）、空边相对好但最高 1.245 仍 <1.3；上一轮「spread both 1.065」只是 0.892+1.245 的糊墙平均。
- **通用规则**：凡同时含多空的因果回测，**主表必须分边**；混池 PF 不得作裁决。怀疑方向写反时，先加「触发条件 ↔ 开仓符号」审计计数，再决定是否改规则。
- **牵连**：`scripts/launch_entry_base_rate.py`；`analysis/p_launch_entry_long_short.md`（主）；`analysis/p_launch_entry_base_rate.md`（混池，已降权）。

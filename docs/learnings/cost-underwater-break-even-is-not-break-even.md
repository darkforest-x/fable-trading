# 名义保本必须先扣完整退出成本

- **问题**：Pine 把达到 +1.5% 后锁定入场价上方 +0.1% 称为 break-even，但项目固定往返成本是
  0.2%。final 的 49 笔该类退出全部精确为毛 +10 bp、净 -10 bp；名字与经济结果相反。
- **死胡同**：看到 stop 高于 entry 就把它归为“保本”，或只统计 gross PnL。另一个死胡同是立刻把
  offset 改到 +0.2% 并重跑；这会改障碍、改变退出时点、后续持仓和 cooldown，不能用静态加 10 bp
  冒充新回测。
- **有效路径**：先冻结退出路径，把每个 stop fill 拆成毛收益、双边成本和净收益；再做仅用于量级的
  same-exit accounting。即使把 49 笔静态抬到净 0，均值也只增 4.45 bp/笔，去掉最大赢家后仍为
  -0.27 bp/笔，说明保本语义错误真实存在，却不是尾部依赖的主因。
- **通用规则**：任何名为 break-even 的止损必须满足 `locked_gross_bp >= round_trip_cost_bp + slippage_bp`
  才能叫成本后保本。实际改 offset/trigger 前按障碍变更处理：owner 批准、单变量、完整状态机重放，
  禁止用静态替换结果宣传收益改善。
- **牵连**：`scripts/analyze_pine_eth_15m_exit_anatomy.py`、
  `experiments/active/exp-pine-eth-15m-v1/results/exit_anatomy.json`、冻结 20 bp 成本、障碍参数 owner 门。

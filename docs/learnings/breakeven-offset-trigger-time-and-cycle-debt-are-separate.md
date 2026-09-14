# 保本价偏移、触发时间与整轮回本必须分开核对

- **问题**：Owner质疑ETH3m倍投连亏统计，并提出“保本的时候提高0.2%”。此前9连亏是1000U按1、2、4…承担风险的容量示例，不是新退出规则已观察到的历史连亏；不能把容量推演写成该策略已发生的事实。
- **死胡同**：将所有“保本”当作同一退出。旧费用保护虽然方向正确，但条件是扣20bp后浮盈达到trigger_r，且保护下一bar才生效；这不等于毛浮盈达到同一个R，也不等于盘中立即移动。旧统计不能直接替代未冻结的新触发定义。
- **有效路径**：只读核对spike_recovery_exit.py与spike_recovery_cash.py，独立审查给出同样结论：保护价为多头entry×1.002、空头entry×0.998，按有利方向取tick；cost_r=.002/initial_risk_frac，触发毛R≥cost_r+trigger_r。净收益只扣一次成本，现金准入费用预留不再次扣款。本bar先检查TP/旧止损，再安装下一bar保护。未运行新策略、未读取新holdout。
- **通用规则**：分别固定保护价、触发阈值的毛/净口径、生效时刻、完整平仓目标的毛/净口径和复位条件。初始价格风险0.1%时，毛1R仅0.1%，不足覆盖0.2%的研究成本；本笔净保本也不偿还之前净债务。若要求整轮回本后重置，只能以累计实际净现金≥0判断。旧be_or_win允许价格保本后认亏重启，不能冒充净回本。
- **可达性检查**：审查曾提出“费用保护越过固定TP后，两者在下根开盘同时触发”的疑似bug，已撤回：trigger_r非负时，能武装这种保护的价格必先命中TP，回放已return，不能留下已武装仓位。没有据不可达组合改代码。
- **牵连**：`yoyo/evaluation/spike_recovery_exit.py:208`、`:279`、`yoyo/evaluation/spike_recovery_cash.py:94`、`:140`；旧预登记为`experiments/active/exp-spike-eth3m-recovery-20260914-v2/PROJECT_PLAN.md`。20bp为固定研究成本，不等于Owner实际交易所费率；止损触发价格也不保证成交价格。[OKX费用说明](https://www.okx.com/en-gb/help/how-to-calculate-the-contract-transaction-fee)、[OKX止损说明](https://www.okx.com/en-gb/help/how-do-i-modify-take-profit-tp-and-stop-loss-sl)。
